"""Real-podcast local validation, no dotenv/credentials/paid requests/deletion."""
import json
import logging
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN']='1'
from src.config import AppConfig
from src.models import Transcript
from src.semantic import retrieve_pairs
from src.narrative import NarrativeCurator
from src.jev import JevTriage
from src.renderer import ShortsRenderer
from src.quality_review import write_quality_review
from src.utils import new_run_dir,write_json


def main():
    folder=new_run_dir(ROOT/'data/validation/narrative')
    print(folder,flush=True)
    (folder/'models').symlink_to(ROOT/'data/models',target_is_directory=True)
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(folder/'run.log'),logging.StreamHandler()])
    cfg=AppConfig(project_root=ROOT,data_dir=folder,temp_dir=folder/'tmp',subtitles_dir=folder/'subtitles',
        clips_dir=folder/'clips',semantic_montage_enabled=True,semantic_allow_download=True,
        hook_preview_enabled=True,silence_jump_cuts_enabled=True,ollama_timeout_seconds=600)
    transcript=Transcript.from_dict(json.loads((ROOT/'data/transcripts/davy-jones-igor-3k-free.transcript.json').read_text()))
    source=ROOT/'data/downloads/davy-jones.mp4'
    start=time.perf_counter()
    blocks,clusters,pairs=retrieve_pairs(transcript,cfg)
    metrics=dict(semantic_seconds=time.perf_counter()-start,block_count=len(blocks),cluster_count=len(clusters),pairs=pairs)
    write_json(folder/'semantic-index.json',dict(blocks=blocks,clusters=clusters,pairs=pairs))
    gate=JevTriage(cfg)  # no key by construction: check local fallback, never send a request
    assert all(d['route']=='local' for d in gate.evaluate(pairs,blocks))
    metrics['jev_live_test']=False
    metrics['jev_reserved_usd']=gate.reserved_usd
    write_json(folder/'measurements.json',metrics)
    curator=NarrativeCurator(cfg)
    plans=[]
    # One known story + three distant-passage retrievals. No preset edit boundaries.
    jobs=[([(3756.8,3788.0)],'Por que Pragmata vendeu bem, apesar de ser uma franquia nova')]
    jobs += [([(blocks[p[k]]['start'],blocks[p[k]]['end']) for k in ('a','b')],
               'Retomada do mesmo assunto; só montar se houver narrativa completa') for p in pairs[3:6]]
    for intervals,title in jobs:
        start=time.perf_counter()
        try:
            clip=curator.plan_story(transcript,intervals,title)
        except (ValueError,RuntimeError,KeyError,TypeError) as exc:
            metrics.setdefault('failures',[]).append(str(exc));clip=None
        metrics.setdefault('editor_seconds',[]).append(time.perf_counter()-start)
        if clip and clip.score>=cfg.virality_min_score:
            plans.append(clip)
        write_json(folder/'plans.json',{'clips':[c.to_dict() for c in plans]})
        write_json(folder/'audit.json',curator.audit)
        write_json(folder/'measurements.json',metrics)
    curator.unload_model()
    if plans:
        # Prefer a genuinely discontiguous story, then a copied hook.
        clip=max(plans,key=lambda c:(len([p for p in c.source_spans if p.role=='body']),len(c.source_spans)))
        renderer=ShortsRenderer(cfg)
        start=time.perf_counter()
        result=renderer.render_single_clip(source,transcript,clip,folder,'narrative')
        metrics['render_seconds']=time.perf_counter()-start
        metrics['output']=str(result.output_path)
        write_quality_review(folder,transcript,[clip],[result],source)
        write_json(folder/'measurements.json',metrics)
    print('COMPLETE',folder,flush=True)


if __name__=='__main__':
    main()
