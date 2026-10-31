import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api_budget import ApiBudget, BudgetExceeded
from src.config import AppConfig
from src.curator import OllamaCurator
from src.models import ClipCandidate, Transcript, TranscriptSegment, WordTimestamp
from src.quality_review import refine_render_clips, write_quality_review
from src.stage_cache import StageCache, digest, file_digest
from src.transcriber import WhisperTranscriber
from src.utils import new_run_dir, safe_rmtree, safe_unlink, write_json


def sample():
    return Transcript('pt', timing_source='faster-whisper', segments=[
        TranscriptSegment(10, 0, 1, 'Gancho.', [WordTimestamp('Gancho.', 0, 1)]),
        TranscriptSegment(20, 1, 2, 'Conclusão.', [WordTimestamp('Conclusão.', 1, 2)]),
        TranscriptSegment(30, 3, 4, 'Outro.', [WordTimestamp('Outro.', 3, 4)])])


def config(tmp_path, **kwargs):
    return AppConfig(data_dir=tmp_path, clip_min_seconds=1, clip_max_seconds=10, **kwargs)


def test_checkpoint_corruption_and_multiple_revisions_are_preserved(tmp_path):
    cache = StageCache(tmp_path)
    key = digest({'stage': 1})
    cache.put('test', key, {'value': 1})
    folder = cache.root / 'test' / key
    bad = folder / 'zz-invalid.json'
    bad.write_text('{unfinished')
    assert cache.get('test', key) == {'value': 1}
    assert bad.exists()
    cache.put('test', key, {'value': 1})
    assert len(list(folder.glob('*.json'))) == 3
    assert StageCache(tmp_path, False).get('test', key) is None


def test_source_fingerprint_detects_same_size_replacement(tmp_path):
    audio = tmp_path / 'audio.wav'
    audio.write_bytes(b'1234'); before = file_digest(audio)
    audio.write_bytes(b'4321')
    assert file_digest(audio) != before


def test_asr_resumes_without_model_and_invalidates_model_setting(tmp_path, monkeypatch):
    audio = tmp_path / 'audio.wav'; audio.write_bytes(b'fixture')
    worker = WhisperTranscriber(config(tmp_path))
    calls = []
    monkeypatch.setattr(worker, '_transcribe_once', lambda **kw: calls.append(kw) or sample())
    worker.transcribe(audio, defer_alignment=True)
    worker.transcribe(audio, defer_alignment=True)
    assert len(calls) == 1
    worker.config.whisper_model = 'other-model'
    worker.transcribe(audio, defer_alignment=True)
    assert len(calls) == 2


def test_alignment_merges_overlapping_clip_requests_and_is_cached(tmp_path, monkeypatch):
    audio = tmp_path / 'audio.wav'; audio.touch()
    worker = WhisperTranscriber(config(tmp_path))
    calls = []
    def align(t, path, selected_ids=None):
        calls.append(selected_ids)
        t.timing_source = 'whisperx-partial'
        return t
    monkeypatch.setattr(worker, 'align_transcript', align)
    clips = [ClipCandidate('a', 0, 2), ClipCandidate('b', .5, 2)]
    worker.align_selected(sample(), audio, clips)
    worker.align_selected(sample(), audio, clips)
    assert calls == [{10, 20}]


def test_id_references_override_invented_times_and_validate_evidence(tmp_path):
    curator = OllamaCurator(config(tmp_path))
    item = dict(title='História', start=50, end=90, start_segment_id=10,
                end_segment_id=20, hook_segment_id=10, payoff_segment_id=20, score=9)
    result = curator._to_candidates({'clips': [item]}, sample())
    assert [(c.start, c.end) for c in result] == [(0, 2)]
    assert 'Gancho.' in result[0].reason and 'Conclusão.' in result[0].reason
    item['payoff_segment_id'] = 30
    assert curator._to_candidates({'clips': [item]}, sample()) == []
    item['start_segment_id'] = 999
    assert curator._to_candidates({'clips': [item]}, sample()) == []


def test_local_review_veto_and_evidence_validation(tmp_path, monkeypatch):
    curator = OllamaCurator(config(tmp_path))
    clips = [ClipCandidate('a', 0, 2, score=9), ClipCandidate('b', 3, 4, score=9)]
    answers = iter([dict(keep=True, hook_id=10, payoff_id=20, hook=9, clarity=9, payoff=9, reason='Completo'),
                    dict(keep=False, reason='Sem contexto')])
    monkeypatch.setattr(curator, '_generate_json', lambda *a: json.dumps(next(answers)))
    result = curator._review_candidates(clips, sample())
    assert len(result) == 1 and result[0].tags == ['reviewed-local']
    monkeypatch.setattr(curator, '_generate_json', lambda *a: json.dumps(
        dict(keep=True, hook_id=999, payoff_id=999, hook=10, clarity=10, payoff=10, reason='Inválido')))
    result = curator._review_candidates(clips, sample())
    assert all('review-incomplete' in c.tags for c in result)
    assert all(c.score == 9 for c in result)


def test_unavailable_review_preserves_candidates(tmp_path, monkeypatch):
    curator = OllamaCurator(config(tmp_path))
    def fail(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(curator, '_generate_json', fail)
    clips = [ClipCandidate('a', 0, 2, score=9), ClipCandidate('b', 3, 4, score=9)]
    assert len(curator._review_candidates(clips, sample())) == 2


def test_reviewer_separates_neighboring_unfinished_turn(tmp_path, monkeypatch):
    transcript = sample()
    transcript.segments[2].text = 'Mas o que acontece com...'
    curator = OllamaCurator(config(tmp_path))
    seen = []
    def review(prompt, schema):
        # A complete payoff must not inherit the next speaker's unfinished turn.
        payload = json.loads(prompt[prompt.index('{"selected_clip"'):])
        seen.append(payload)
        return json.dumps(dict(keep=True, hook_id=10, payoff_id=20,
                               hook=9, clarity=9, payoff=9, reason='Concluído'))
    monkeypatch.setattr(curator, '_generate_json', review)
    clips = [ClipCandidate('Completo', 0, 2, score=9), ClipCandidate('Outro', 3, 4, score=8)]
    result = curator._review_candidates(clips, transcript)
    assert [s['id'] for s in seen[0]['selected_clip']] == [10, 20]
    assert seen[0]['outside_after'] == [{'id': 30, 'text': 'Mas o que acontece com...'}]
    assert result[0].score == 9 and 'reviewed-local' in result[0].tags


def test_api_reservations_accumulate_and_unknown_prices_fail_closed():
    budget = ApiBudget(.01)
    budget.reserve('model', 'Olá', 1000, 1, 1)
    assert budget.reserved_usd > 0
    with pytest.raises(BudgetExceeded):
        budget.reserve('model', 'x' * 10000, 1000, 1, 1)
    assert len(budget.calls) == 1
    with pytest.raises(BudgetExceeded):
        ApiBudget(.05).reserve('unknown', 'text', 1000, 0, 0)


def test_paid_model_must_match_configured_tariff(tmp_path):
    curator = OllamaCurator(config(tmp_path, api_priced_model='priced',
                                  api_input_usd_per_million=1, api_output_usd_per_million=1))
    with pytest.raises(BudgetExceeded):
        curator._reserve_paid_call('different-fallback', 'text')
    assert not curator.budget.calls


def test_generation_directories_and_json_history_preserve_outputs(tmp_path):
    first, second = new_run_dir(tmp_path), new_run_dir(tmp_path)
    assert first != second
    output = first / 'result.json'
    write_json(output, {'original': True})
    write_json(output, {'original': False})
    assert json.loads(next((first / '.history').glob('*.json')).read_text()) == {'original': True}
    assert not safe_unlink(output) and output.exists()
    assert not safe_rmtree(first) and first.exists()


def test_word_boundary_refinement_does_not_chop_to_fit(tmp_path):
    t = sample()
    clips = [ClipCandidate('a', .2, 1.8)]
    result = refine_render_clips(clips, t, config(tmp_path))
    assert (result[0].start, result[0].end) == (0, 2)
    cfg = config(tmp_path); cfg.clip_max_seconds = 1.8
    assert not refine_render_clips(clips, t, cfg)


def test_review_page_escapes_transcript_and_title(tmp_path):
    t = sample(); t.segments[0].text = '<script>bad()</script>'
    path = write_quality_review(tmp_path, t, [ClipCandidate('<img src=x>', 0, 2)])
    text = path.read_text()
    assert '<script>bad()' not in text and '&lt;img src=x&gt;' in text
    assert json.loads((tmp_path / 'quality-review.json').read_text())['clips'][0]['word_count'] == 2


def test_caption_connectors_stay_with_following_word():
    from src.captions import BrainRotCaptionBuilder
    words = [WordTimestamp(w, i*.2, (i+1)*.2) for i,w in enumerate(['Eu', 'vi', 'a', 'casa'])]
    chunks = BrainRotCaptionBuilder._chunk_words(words, 3, 28)
    assert [[w.text for w in group] for group in chunks] == [['Eu', 'vi'], ['a', 'casa']]
    assert [w for group in chunks for w in group] == words


def workflow_config(tmp_path):
    return AppConfig(project_root=tmp_path, data_dir=tmp_path / 'data',
                     downloads_dir=tmp_path / 'data/downloads', audio_dir=tmp_path / 'data/audio',
                     transcripts_dir=tmp_path / 'data/transcripts', clips_dir=tmp_path / 'data/clips',
                     subtitles_dir=tmp_path / 'data/subtitles', temp_dir=tmp_path / 'data/tmp',
                     clip_min_seconds=1, clip_max_seconds=10, ollama_clips_count=1)


def test_cli_integrates_selective_alignment_and_preserves_previous_runs(tmp_path, monkeypatch):
    import sys
    import main
    from src.renderer import RenderResult
    cfg = workflow_config(tmp_path)
    video, audio = tmp_path/'video.mp4', tmp_path/'audio.wav'
    video.touch(); audio.touch()
    old = cfg.clips_dir/'profile_3/video/old.mp4'
    old.parent.mkdir(parents=True); old.write_bytes(b'keep')
    monkeypatch.setattr(main, 'load_config', lambda **kw: cfg)
    monkeypatch.setattr(main, 'validate_runtime_dependencies', lambda **kw: None)
    monkeypatch.setattr(main, 'release_gpu_memory', lambda: None)
    monkeypatch.setattr(main, '_run_optional_upscaling', lambda **kw: [])
    calls = []
    def transcribe(**kwargs):
        assert kwargs['defer_alignment']
        return sample()
    monkeypatch.setattr(main, 'transcribe_audio', transcribe)
    def align(self, t, path, clips):
        calls.append('align'); return t
    monkeypatch.setattr(main.WhisperTranscriber, 'align_selected', align)
    def curate(t):
        calls.append('curate')
        return SimpleNamespace(clips=[ClipCandidate('a', 0, 2, score=9)], parsed_ok=True, warning=None)
    monkeypatch.setattr(main, 'get_curator', lambda cfg: SimpleNamespace(curate=curate, unload_model=lambda: None))
    class Renderer:
        def __init__(self, cfg):
            pass
        def render_clips(self, **kwargs):
            output = kwargs['output_dir']/'clip.mp4'; output.write_bytes(b'new')
            return [RenderResult(kwargs['clips'][0], output)]
    monkeypatch.setattr(main, 'ShortsRenderer', Renderer)
    monkeypatch.setattr(sys, 'argv', ['main.py', '--input', str(video), '--audio-path', str(audio), '--slug', 'video', '--yes'])
    assert main.main() == 0
    assert main.main() == 0
    assert old.read_bytes() == b'keep'
    assert len(list(old.parent.glob('run-*/clip.mp4'))) == 2
    assert len(list(old.parent.glob('run-*/review.html'))) == 2
    assert calls.count('curate') == 1  # Curation checkpoint reused.
    assert calls.count('align') == 2  # Alignment method owns its own stage cache.


def test_dry_run_without_candidates_saves_diagnostic_and_preserves_previous_clips(tmp_path,monkeypatch,capsys):
    import sys
    import main
    cfg=workflow_config(tmp_path)
    cfg.ensure_directories()
    video,audio=tmp_path/'video.mp4',tmp_path/'audio.wav'
    video.touch();audio.touch()
    transcript=tmp_path/'transcript.json'
    transcript.write_text(json.dumps(sample().to_dict()))
    previous=cfg.transcripts_dir/'video.clips.json';previous.write_text('previous generation')
    monkeypatch.setattr(main,'load_config',lambda **kw:cfg)
    monkeypatch.setattr(main,'validate_runtime_dependencies',lambda **kw:None)
    monkeypatch.setattr(main,'release_gpu_memory',lambda:None)
    monkeypatch.setattr(main,'get_curator',lambda cfg:SimpleNamespace(
        curate=lambda t:SimpleNamespace(clips=[],warning='No narrative passed; inspect audit'),unload_model=lambda:None))
    monkeypatch.setattr(main,'ShortsRenderer',lambda *a:pytest.fail('No candidates must not render'))
    monkeypatch.setattr(sys,'argv',['main.py','--input',str(video),'--audio-path',str(audio),
        '--transcript-json',str(transcript),'--slug','video','--dry-run'])
    assert main.main()==0
    diagnostic=json.loads(next((cfg.data_dir/'reviews/video').glob('run-*/diagnostic.json')).read_text())
    assert diagnostic['status']=='no_candidates' and diagnostic['clips_count']==0
    assert 'inspect audit' in diagnostic['message']
    assert Path(diagnostic['review']).exists()
    assert previous.read_text()=='previous generation'


def test_service_integrates_same_selective_flow(tmp_path):
    from src.pipeline import ShortsPipelineService, PipelineRequest
    from src.downloader import DownloadResult
    from src.renderer import RenderResult
    cfg = workflow_config(tmp_path)
    cfg.ensure_directories()
    video, audio = tmp_path/'video.mp4', tmp_path/'audio.wav'
    video.touch(); audio.touch()
    service = object.__new__(ShortsPipelineService)
    service.config = cfg
    service._bootstrap = lambda: None
    service.downloader = SimpleNamespace(download=lambda **kw: DownloadResult(video, audio, tmp_path/'info.json', 'test', None, 4, 'local'))
    events = []
    def transcribe(**kwargs):
        assert kwargs['defer_alignment']; events.append('asr'); return sample()
    def align(t, path, clips):
        events.append('align'); return t
    service.transcriber = SimpleNamespace(transcribe=transcribe, unload_model=lambda: None, align_selected=align)
    def curate(t):
        events.append('curate')
        return SimpleNamespace(clips=[ClipCandidate('a', 0, 2, score=9)], parsed_ok=True, warning=None, raw_response='')
    service.curator = SimpleNamespace(curate=curate, unload_model=lambda: None)
    def render(**kwargs):
        events.append('render')
        output=kwargs['output_dir']/'clip.mp4'; output.touch()
        return [RenderResult(kwargs['clips'][0], output)]
    service.renderer = SimpleNamespace(render_clips=render)
    result=service.run(PipelineRequest('local', slug='test'))
    assert events == ['asr', 'curate', 'align', 'render']
    assert (result.render_results[0].output_path.parent/'review.html').exists()


def test_recompute_flags_preserve_asr_when_only_curation_is_forced():
    from main import apply_cli_overrides, build_parser
    cfg = apply_cli_overrides(AppConfig(), build_parser().parse_args(['--force-curate']))
    assert cfg.stage_cache_enabled and not cfg.curation_cache_enabled
    cfg = apply_cli_overrides(AppConfig(), build_parser().parse_args(['--no-cache', '--align-full', '--no-editorial-review']))
    assert not cfg.stage_cache_enabled and not cfg.selective_alignment and not cfg.editorial_review_enabled


def test_selected_aligner_never_calls_model_for_unselected_segments(tmp_path, monkeypatch):
    import sys
    import numpy as np
    calls = []
    def align(items, *args, **kwargs):
        calls.append(items[0]['text'])
        return {'segments': [{'words': [{'word': 'Outro.', 'start': 3.1, 'end': 3.9}]}]}
    monkeypatch.setitem(sys.modules, 'whisperx', SimpleNamespace(
        load_align_model=lambda **kw: (object(), {}), load_audio=lambda path: np.zeros(80000), align=align))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setattr('src.transcriber.release_gpu_memory', lambda: None)
    t = sample()
    result = WhisperTranscriber(config(tmp_path)).align_transcript(t, Path('not-read.wav'), selected_ids={30})
    assert calls == ['Outro.']
    assert result.words[0].start == 0 and result.words[-1].start == 3.1
    assert result.timing_source == 'whisperx-partial'


def test_rerender_chooses_metadata_for_exact_generation(tmp_path):
    from src.rerenderer import load_rerender_context
    cfg = workflow_config(tmp_path)
    first, second = new_run_dir(cfg.clips_dir), new_run_dir(cfg.clips_dir)
    clips = [first/'same.mp4', second/'same.mp4']
    for index, clip in enumerate(clips):
        clip.touch()
        meta = cfg.subtitles_dir / f'generation-{index}' / 'same.meta.json'
        meta.parent.mkdir(parents=True)
        meta.write_text(json.dumps(dict(clip_path=str(clip), source_video=str(tmp_path/f'source-{index}.mp4'),
            clip=dict(start=index, end=index+1, title='a'), tracking_path=None, transcript_json=str(tmp_path/'transcript.json'))))
    context = load_rerender_context(cfg, clips[1])
    assert context['source_video'].name == 'source-1.mp4'


def test_local_json_cache_reuses_response_and_force_curate_bypasses(tmp_path, monkeypatch):
    curator = OllamaCurator(config(tmp_path))
    calls = []
    monkeypatch.setattr(curator, '_post_ollama_payload', lambda *args: calls.append(args) or {'response': '{"clips": []}'})
    assert curator._call_ollama_generate('fixture') == '{"clips": []}'
    assert curator._call_ollama_generate('fixture') == '{"clips": []}'
    assert len(calls) == 1
    curator.config.curation_cache_enabled = False
    curator._call_ollama_generate('fixture')
    assert len(calls) == 2


def test_tracking_cache_hit_avoids_decode_and_settings_invalidate(tmp_path, monkeypatch):
    import src.tracking as module
    from src.models import TrackingPath, TrackingPoint
    root = tmp_path/'models'; root.mkdir()
    video = tmp_path/'video.mp4'; video.write_bytes(b'synthetic')
    path = TrackingPath(1920, 1080, 608, 1080, [TrackingPoint(0, 0, 0)])
    stored = []
    class Cache:
        def __init__(self, *a):
            pass
        def get(self, stage, key):
            stored.append(key)
            return path.to_dict()
    monkeypatch.setattr(module, 'run_cmd', lambda *a, **kw: pytest.fail('cache hit must not decode video'))
    monkeypatch.setattr('src.stage_cache.StageCache', Cache)
    tracker = module.SpeakerTracker(model_dir=root)
    result = tracker.track_clip(video, 0, 10)
    assert result.source_width == 1920
    tracker.threshold = .9
    tracker.track_clip(video, 0, 10)
    assert stored[0] != stored[1]
