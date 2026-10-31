"""CPU multilingual retrieval. Similarity proposes pairs; it never approves edits."""
import gc
import logging
import numpy as np
from .stage_cache import StageCache, digest, package_versions

LOGGER = logging.getLogger(__name__)


def transcript_blocks(transcript, max_seconds=45, max_chars=1200):
    result, current, size = [], [], 0
    for seg in transcript.segments:
        if current and (seg.end-current[0].start>max_seconds or size+len(seg.text)>max_chars):
            result.append(current)
            current,size = [],0
        current.append(seg)
        size += len(seg.text)
    if current:
        result.append(current)
    return [dict(id=i,start=s[0].start,end=s[-1].end,start_id=s[0].id,end_id=s[-1].id,
                 text=' '.join(x.text for x in s)) for i,s in enumerate(result)]


def complete_link_clusters(vectors, threshold):
    """Avoid transitive A~B~C chains that join unrelated A and C."""
    similarities = np.asarray(vectors) @ np.asarray(vectors).T
    groups = []
    for i in range(len(vectors)):
        eligible = [(float(min(similarities[i,j] for j in g)), k) for k,g in enumerate(groups)]
        score,index = max(eligible,default=(-1,-1))
        if score >= threshold:
            groups[index].append(i)
        else:
            groups.append([i])
    return groups,similarities


def pair_candidates(blocks, vectors, threshold=.90, limit=12):
    groups,scores = complete_link_clusters(vectors,threshold)
    proposals = []
    for group in groups:
        for n,a in enumerate(group):
            for b in group[n+1:]:
                if blocks[b]['start']-blocks[a]['end']>=60:
                    proposals.append(dict(a=a,b=b,similarity=float(scores[a,b])))
    proposals.sort(key=lambda p:(-p['similarity'],p['a'],p['b']))
    selected,counts = [],{}
    for pair in proposals:
        if any(counts.get(pair[k],0)>=2 for k in ('a','b')):
            continue
        selected.append(pair)
        for k in ('a','b'):
            counts[pair[k]]=counts.get(pair[k],0)+1
        if len(selected)>=limit:
            break
    return groups,selected


def embed_blocks(blocks, config):
    cache = StageCache(config.data_dir,config.stage_cache_enabled)
    key = digest(dict(version=2,model=config.semantic_embedding_model,revision=config.semantic_embedding_revision,
                      texts=[b['text'] for b in blocks],packages=package_versions('sentence-transformers','transformers')))
    cached = cache.get('semantic-embeddings',key)
    if cached is not None:
        vectors = np.asarray(cached,dtype=np.float32)
        if vectors.ndim==2 and len(vectors)==len(blocks) and np.isfinite(vectors).all():
            return vectors
    from sentence_transformers import SentenceTransformer
    import torch
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(min(4,previous_threads))
    model = None
    try:
        model = SentenceTransformer(config.semantic_embedding_model,device='cpu',
            cache_folder=str(config.data_dir/'models/embeddings'),revision=config.semantic_embedding_revision,
            local_files_only=not config.semantic_allow_download,token=False,trust_remote_code=False)
        # Split overlength input by real tokens; no content silently truncated.
        texts,owners = [],[]
        width = max(16,model.max_seq_length-16)
        for index,block in enumerate(blocks):
            ids = model.tokenizer.encode(block['text'],add_special_tokens=False)
            for offset in range(0,max(1,len(ids)),width):
                # E5 model card recommends query: on BOTH sides for symmetric
                # semantic similarity and clustering (not query/document search).
                texts.append('query: '+model.tokenizer.decode(ids[offset:offset+width],skip_special_tokens=True))
                owners.append(index)
        encoded = model.encode(texts,batch_size=8,normalize_embeddings=True,show_progress_bar=False)
        vectors = np.zeros((len(blocks),encoded.shape[1]),dtype=np.float32)
        for owner,vector in zip(owners,encoded):
            vectors[owner] += vector
        vectors /= np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-9)
        cache.put('semantic-embeddings',key,vectors.tolist())
        return vectors
    finally:
        del model
        gc.collect()
        torch.set_num_threads(previous_threads)


def retrieve_pairs(transcript,config):
    blocks = transcript_blocks(transcript)
    if not blocks:
        return [],[],[]
    vectors = embed_blocks(blocks,config)
    groups,pairs = pair_candidates(blocks,vectors,config.semantic_similarity_threshold,config.semantic_max_pairs)
    LOGGER.info('Semantic retrieval: %d blocks, %d clusters, %d distant pairs',len(blocks),len(groups),len(pairs))
    return blocks,groups,pairs
