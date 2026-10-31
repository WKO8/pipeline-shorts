import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from src.config import AppConfig
from src.models import ClipCandidate, SourceSpan, Transcript, TranscriptSegment, WordTimestamp
from src.narrative import validate_plan, source_overlap, NarrativeCurator
from src.montage import remap_transcript, prepare_montage
from src.semantic import transcript_blocks, pair_candidates, complete_link_clusters
from src.jev import JevTriage
from src.renderer import ShortsRenderer


def story():
    texts = ['Eu tropecei na rua.', 'Um homem me ajudou.', 'Vi uma arma e fiquei em choque.',
             'Depois conversamos sobre o tempo.', 'Agradeci e vi o distintivo.']
    return Transcript('pt',segments=[TranscriptSegment(i,i*10,i*10+8,t,
        [WordTimestamp(t,i*10,i*10+8)]) for i,t in enumerate(texts)])


def plan():
    return dict(keep=True,complete=True,title='O susto e o distintivo',reason='História resolvida',score=9,
        parts=[dict(start_id=0,end_id=2),dict(start_id=4,end_id=4)],
        payoff_id=4,payoff_quote='vi o distintivo.',preview=dict(start_id=2,end_id=2))


def cfg(tmp_path,**kw):
    return AppConfig(data_dir=tmp_path,hook_preview_enabled=True,**kw)


def test_narrative_preserves_payoff_omits_digression_and_repeats_hook(tmp_path):
    transcript = story()
    clip = validate_plan(plan(),transcript,set(range(5)),cfg(tmp_path))
    assert clip.duration==44  # 8s preview + 28s body + 8s ending
    assert clip.source_ranges==[(20,28),(0,28),(40,48)]
    mapped,timeline,duration = remap_transcript(transcript,clip.source_spans,30)
    assert [s.text for s in mapped.segments].count('Vi uma arma e fiquei em choque.')==2
    assert 'tempo' not in mapped.full_text and mapped.segments[-1].text.endswith('distintivo.')
    assert mapped.segments[-1].end==duration==44
    assert len({s.id for s in mapped.segments})==len(mapped.segments)
    assert timeline[0]['segments'][0]['source_id']==2


@pytest.mark.parametrize('change',[
    {'parts':[dict(start_id=4,end_id=4),dict(start_id=0,end_id=2)]},
    {'preview':dict(start_id=3,end_id=3)},
    {'payoff_id':3}, {'payoff_quote':'Não estava armado.'},
    {'parts':[dict(start_id=0,end_id=99)]}, {'score':float('nan')},
])
def test_invalid_source_or_false_payoff_fails_closed(tmp_path,change):
    payload = plan()|change
    with pytest.raises((ValueError,KeyError)):
        validate_plan(payload,story(),set(range(5)),cfg(tmp_path))


def test_hook_counts_toward_duration_and_does_not_truncate_ending(tmp_path):
    with pytest.raises(ValueError,match='duration'):
        validate_plan(plan(),story(),set(range(5)),cfg(tmp_path,clip_max_seconds=40))


def test_part_cannot_cross_missing_evidence(tmp_path):
    with pytest.raises(ValueError,match='unavailable context'):
        validate_plan(plan(),story(),{0,2,4},cfg(tmp_path))


def test_independent_editor_rejects_misleading_montage(tmp_path,monkeypatch):
    curator = NarrativeCurator(cfg(tmp_path))
    answers = iter([plan(),dict(decision='rejeitar',reason='Ressalva removida',evidence_quote='distintivo')])
    monkeypatch.setattr(curator,'_ask',lambda *args:next(answers))
    assert curator._legacy_plan_story(story(),[(0,48)]) is None


def test_overlap_uses_actual_passages_not_hour_long_envelope():
    a = ClipCandidate('a',0,3600,source_spans=[SourceSpan(0,20),SourceSpan(3580,3600)])
    b = ClipCandidate('b',100,140)
    assert source_overlap(a,b)==0
    c = ClipCandidate('c',0,20)
    assert source_overlap(a,c)==1


def test_editor_uses_explicit_approval_not_legacy_contradictory_booleans(tmp_path,monkeypatch):
    curator=NarrativeCurator(cfg(tmp_path))
    answers=iter([
        dict(standalone=False,complete=True,faithful=True,reason='Nenhuma falha de edição.'),
        dict(decision='aprovar',reason='História resolvida.',evidence_quote=''),
    ])
    monkeypatch.setattr(curator,'_ask',lambda *a:next(answers))
    assert curator.review_story('O susto',[dict(text='Vi o distintivo.',role='body')])['decision']=='aprovar'


@pytest.mark.parametrize('verdict',[
    dict(decision='aprovar',reason='A transcrição apresenta falhas de coerência e sentido.',evidence_quote=''),
    dict(decision='rejeitar',reason='Falta explicação.',evidence_quote='frase inventada'),
    dict(decision='aprovar',reason='Nenhum problema.',evidence_quote='distintivo'),
    dict(decision='aprovar',reason='',evidence_quote=''),
])
def test_inconsistent_editor_verdict_is_never_accepted(tmp_path,monkeypatch,verdict):
    curator=NarrativeCurator(cfg(tmp_path))
    monkeypatch.setattr(curator,'_ask',lambda *a:verdict)
    with pytest.raises(ValueError):
        curator.review_story('O susto',[dict(text='Vi o distintivo.',role='body')])


def test_inconclusive_editor_does_not_approve_plan(tmp_path,monkeypatch):
    curator=NarrativeCurator(cfg(tmp_path))
    answers=iter([plan(),dict(decision='incerto',reason='Não consigo resolver o referente.',evidence_quote='')])
    monkeypatch.setattr(curator,'_ask',lambda *a:next(answers))
    assert curator._legacy_plan_story(story(),[(0,48)]) is None


def test_rejection_accepts_quote_wrappers_but_preserves_literal_evidence(tmp_path,monkeypatch):
    curator=NarrativeCurator(cfg(tmp_path))
    monkeypatch.setattr(curator,'_ask',lambda *a:dict(decision='rejeitar',reason='Referente ausente.',
        evidence_quote='“Vi o distintivo.”'))
    assert curator.review_story('O susto',[dict(text='Vi o distintivo.',role='body')])['evidence_quote']=='Vi o distintivo.'


def test_planner_context_marks_gaps_as_separate_windows(tmp_path):
    curator=NarrativeCurator(cfg(tmp_path))
    selected,text=curator._context(story(),[(0,8),(40,48)],0)
    assert [s.id for s in selected]==[0,4]
    assert 'IDs 0–0 (8.00s)' in text and 'IDs 4–4 (8.00s)' in text


def test_context_shrinks_only_margins_to_fit_body_plus_hook(tmp_path):
    t=Transcript('pt',segments=[TranscriptSegment(i,i*10,i*10+10,'Uma frase completa.') for i in range(40)])
    curator=NarrativeCurator(cfg(tmp_path,clip_max_seconds=180,hook_preview_max_seconds=10))
    selected,_=curator._context(t,[(100,140),(300,340)],45)
    assert sum(s.end-s.start for s in selected)<=170
    assert set(range(10,14))|set(range(30,34)) <= {s.id for s in selected}
    with pytest.raises(ValueError,match='without margins'):
        curator._context(t,[(0,200)],45)


def test_rejected_valid_plan_is_available_for_review_not_rendering(tmp_path,monkeypatch):
    curator=NarrativeCurator(cfg(tmp_path))
    answers=iter([plan(),dict(decision='incerto',reason='Conexão incerta.',evidence_quote='')])
    monkeypatch.setattr(curator,'_ask',lambda *a:next(answers))
    assert curator._legacy_plan_story(story(),[(0,48)]) is None
    assert len(curator.proposals)==1
    assert curator.proposals[0]['status']=='incerto'
    assert curator.proposals[0]['clip'].source_ranges==[(20,28),(0,28),(40,48)]


def test_equivalent_cli_and_direct_numbers_have_identical_prompts(tmp_path,monkeypatch):
    prompts=[]
    for minimum,maximum,hook in [(30,180,10),(30.0,180.0,10.0)]:
        curator=NarrativeCurator(cfg(tmp_path,clip_min_seconds=minimum,clip_max_seconds=maximum,hook_preview_max_seconds=hook))
        def ask(prompt,schema):
            prompts.append(prompt);return {'keep':False}
        monkeypatch.setattr(curator,'_ask',ask)
        assert curator._legacy_plan_story(story(),[(0,48)]) is None
    assert prompts[0]==prompts[1]


def test_semantic_clustering_does_not_chain_unrelated_endpoints():
    v=np.array([[1,0],[.8,.6],[.28,.96]])
    groups,_=complete_link_clusters(v,.75)
    assert groups==[[0,1],[2]]
    blocks=[dict(start=i*200,end=i*200+30) for i in range(3)]
    _,pairs=pair_candidates(blocks,v,.75)
    assert [(p['a'],p['b']) for p in pairs]==[(0,1)]


def test_every_transcript_segment_is_covered_by_semantic_blocks():
    t=story();b=transcript_blocks(t,max_seconds=15,max_chars=40)
    assert [s.id for block in b for s in t.segments if block['start']<=s.start and s.end<=block['end']]==list(range(5))


def test_silence_threshold_padding_protects_quiet_words(tmp_path,monkeypatch):
    renderer=object.__new__(ShortsRenderer)
    renderer.config=AppConfig(silence_padding_seconds=.15)
    monkeypatch.setattr(renderer,'_detect_silence_windows',lambda **kw:[(1,3)])
    t=Transcript('pt',segments=[TranscriptSegment(0,1.8,2.1,'fala baixa',[WordTimestamp('baixa',1.8,2.1)])])
    windows=renderer._build_jump_keep_windows(tmp_path/'source.mp4',ClipCandidate('a',0,4),t)
    assert any(a<=1.65 and b>=2.25 for a,b in windows)
    assert windows[0][1]>=1.15 and windows[-1][0]<=2.85
    assert sum(b-a for a,b in windows)<4


def test_cli_flags_preserve_visual_configuration():
    from main import apply_cli_overrides,build_parser
    original=AppConfig()
    changed=apply_cli_overrides(AppConfig(),build_parser().parse_args(['--semantic-montage','--jev-triage',
        '--hook-preview','--remove-silence','--silence-threshold-db','-38','--silence-min-seconds','1',
        '--silence-padding-seconds','.2']))
    assert changed.semantic_montage_enabled and changed.jev_enabled and changed.hook_preview_enabled
    assert changed.silence_noise_db==-38 and changed.silence_padding_seconds==.2
    for field in ('captions_font_size','tracking_eye_line_ratio','cinematic_zoom','output_fps','ffmpeg_nvenc_preset'):
        assert getattr(changed,field)==getattr(original,field)


def test_montage_roundtrip_and_selective_alignment_only_touch_used_segments(tmp_path,monkeypatch):
    from main import save_clip_candidates,load_clip_candidates
    from src.transcriber import WhisperTranscriber
    clip=ClipCandidate('a',0,48,source_spans=[SourceSpan(40,48,'hook'),SourceSpan(0,8),SourceSpan(40,48)])
    path=tmp_path/'clips.json';save_clip_candidates(path,[clip])
    loaded=load_clip_candidates(path)[0]
    assert loaded.to_dict()==clip.to_dict() and loaded.duration==24
    asr=WhisperTranscriber(AppConfig(data_dir=tmp_path,stage_cache_enabled=False))
    seen=[]
    def align(t,a,selected_ids=None):
        seen.append(selected_ids);return t
    monkeypatch.setattr(asr,'align_transcript',align)
    audio=tmp_path/'source.wav';audio.write_bytes(b'audio fixture')
    asr.align_selected(story(),audio,[loaded])
    assert seen==[{0,4}]  # not the 30 seconds between them; preview aligned just once


def test_jev_gate_cannot_be_bypassed_when_every_pair_is_rejected(tmp_path,monkeypatch):
    import src.semantic
    import src.jev
    curator=NarrativeCurator(AppConfig(data_dir=tmp_path,jev_enabled=True))
    blocks=[dict(start=0,end=8),dict(start=40,end=48)]
    monkeypatch.setattr(src.semantic,'retrieve_pairs',lambda *a:(blocks,[[0,1]],[dict(a=0,b=1)]))
    monkeypatch.setattr(src.jev.JevTriage,'evaluate',lambda *a:[dict(route='reject')])
    monkeypatch.setattr(curator,'_global_cards',lambda *a:pytest.fail('Veto bypassed'))
    monkeypatch.setattr(curator,'plan_story',lambda *a:pytest.fail('Rejected pair reached editor'))
    assert not curator.curate(story()).clips


def test_narrative_reasoning_is_bounded_and_separate_from_fast_cache(tmp_path,monkeypatch):
    from src.curator import OllamaCurator
    curator=OllamaCurator(AppConfig(data_dir=tmp_path))
    payloads=[]
    def post(url,payload):
        payloads.append(payload);return {'response':'{"ok":true}'}
    monkeypatch.setattr(curator,'_post_ollama_payload',post)
    schema={'type':'object','properties':{'ok':{'type':'boolean'}}}
    curator._generate_json('test',schema,thinking=True)
    curator._generate_json('test',schema)
    assert len(payloads)==2
    assert payloads[0]['think'] is True and payloads[0]['options']['num_predict']==4096
    assert payloads[0]['options']['num_ctx']==8192


def test_empty_reasoning_retries_once_without_bypassing_validation(tmp_path,monkeypatch):
    from src.narrative import PLAN_SCHEMA
    curator=NarrativeCurator(cfg(tmp_path));curator.audit=[]
    modes=[]
    def generate(prompt,schema,thinking=False):
        modes.append(thinking)
        if thinking:
            raise RuntimeError("Ollama returned empty 'response'.")
        return json.dumps({'keep':False,'parts':[]})
    monkeypatch.setattr(curator,'_generate_json',generate)
    assert curator._ask('evidence',PLAN_SCHEMA)['keep'] is False
    assert modes==[True,False]
    assert curator.audit[0]['local_fallback']=='empty_reasoning_response'
    curator._ask('next evidence',PLAN_SCHEMA)
    assert modes==[True,False,False]  # do not exhaust the same budget for every pair


def test_jev_rejects_malformed_probability_without_caching(tmp_path,monkeypatch):
    client,pairs,blocks=jev_fixture(tmp_path)
    bad=response('unrelated');bad['answers']['pair_0']['probabilities']['unrelated']=float('nan')
    monkeypatch.setattr('src.jev.requests.post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:bad))
    assert client.evaluate(pairs,blocks)[0]['route']=='local'
    assert not list(tmp_path.rglob('*.json'))


def test_semantic_mode_uses_local_editor_even_if_regular_provider_is_paid():
    from src.curator import get_curator
    assert isinstance(get_curator(AppConfig(curator_provider='deepseek',semantic_montage_enabled=True)),NarrativeCurator)


def jev_fixture(tmp_path,**kw):
    config=AppConfig(data_dir=tmp_path,jev_enabled=True,typesafe_api_key='test-only-not-a-real-key',**kw)
    return JevTriage(config),[dict(a=0,b=1,similarity=.9)],[dict(text='Pergunta sobre Minecraft.'),dict(text='Resposta sobre Minecraft.')]


def response(choice='compatible',confidence=.9):
    probs={k:.025 for k in ('compatible','unrelated','needs_context')};probs[choice]=.95
    return dict(model='jev-1.13.0',answers={'pair_0':dict(type='choice',choice=choice,confidence=confidence,probabilities=probs)},usage={'input_tokens':100})


def test_jev_batches_questions_caches_and_never_stores_key(tmp_path,monkeypatch):
    client,pairs,blocks=jev_fixture(tmp_path)
    calls=[]
    def post(url,**kw):
        calls.append(kw)
        payload=response();payload['answers']['pair_1']=payload['answers']['pair_0']
        return SimpleNamespace(status_code=200,json=lambda:payload)
    monkeypatch.setattr('src.jev.requests.post',post)
    decisions=client.evaluate(pairs*2,blocks)
    assert len(calls)==1 and len(calls[0]['json']['questions'])==2
    assert len(calls[0]['json']['state']['blocks'])==2  # repeated evidence sent only once
    assert all(d['route']=='local' for d in decisions)  # still needs narrative editing
    client.evaluate(pairs*2,blocks)
    assert len(calls)==1
    assert client.reserved_usd<.01
    for path in tmp_path.rglob('*.json'):
        assert 'test-only-not-a-real-key' not in path.read_text()


@pytest.mark.parametrize('choice,confidence,expected',[('unrelated',.95,'reject'),('unrelated',.3,'local'),('needs_context',.99,'local')])
def test_jev_routes_uncertain_decisions_to_local(tmp_path,monkeypatch,choice,confidence,expected):
    client,pairs,blocks=jev_fixture(tmp_path)
    monkeypatch.setattr('src.jev.requests.post',lambda *a,**kw:SimpleNamespace(status_code=200,json=lambda:response(choice,confidence)))
    assert client.evaluate(pairs,blocks)[0]['route']==expected


def test_jev_budget_and_missing_key_never_issue_requests(tmp_path,monkeypatch):
    client,pairs,blocks=jev_fixture(tmp_path,jev_budget_usd=0)
    monkeypatch.setattr('src.jev.requests.post',lambda *a,**kw:pytest.fail('Unexpected paid call'))
    assert client.evaluate(pairs,blocks)[0]['route']=='local'
    client.config.jev_budget_usd=.01;client.config.typesafe_api_key=None
    assert client.evaluate(pairs,blocks)[0]['route']=='local'


def test_jev_timeout_keeps_reservation_without_retry(tmp_path,monkeypatch):
    import requests
    client,pairs,blocks=jev_fixture(tmp_path)
    calls=[]
    def fail(*a,**kw):
        calls.append(1);raise requests.Timeout()
    monkeypatch.setattr('src.jev.requests.post',fail)
    assert client.evaluate(pairs,blocks)[0]['route']=='local'
    assert len(calls)==1 and client.reserved_usd>0


def test_ffmpeg_montage_repeat_and_av_duration(tmp_path):
    import subprocess
    source=tmp_path/'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-n','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=4',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4','-c:v','libx264','-preset','ultrafast',
        '-c:a','aac','-shortest',str(source)],check=True)
    t=Transcript('pt',segments=[TranscriptSegment(i,i,i+1,str(i),[WordTimestamp(str(i),i,i+1)]) for i in range(4)])
    clip=ClipCandidate('edit',0,4,source_spans=[SourceSpan(2,3,'hook'),SourceSpan(0,1),SourceSpan(2,4)])
    video,mapped,local,manifest=prepare_montage(source,t,clip,tmp_path,AppConfig(clip_min_seconds=1),encoder='libx264')
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','json',str(video)]))
    assert abs(float(probe['format']['duration'])-4)<.04
    assert [s.text for s in mapped.segments]==['2','0','2','3']
    assert source.exists() and len(list(video.parent.glob('part-*.mkv')))==3
    assert json.loads((video.parent/'edit-decision-list.json').read_text())['timeline'][0]['role']=='hook'
