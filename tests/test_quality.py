import json
from pathlib import Path
import subprocess

import numpy as np
import pysubs2
import pytest

from src.active_speaker import choose_speakers
from src.captions import BrainRotCaptionBuilder, KineticCaptionStyle
from src.config import AppConfig
from src.models import TrackingPoint, TrackingPath, Transcript, TranscriptSegment, WordTimestamp
from src.renderer import ShortsRenderer
from src.tracking import boolean_windows


def test_speaker_follows_audio_not_screen_side():
    dets = [{10: None, 20: None}] * 50
    scores = {(i, tid): (0.95 if tid == (10 if i < 25 else 20) else 0.05)
              for i in range(50) for tid in (10,20)}
    selected = choose_speakers(dets, scores, {0})
    assert selected[0] == 10
    assert selected[25] == 10  # brief interruptions don't switch the shot
    assert selected[35] == 20


def test_ambiguous_audio_and_missing_faces_preserve_scene():
    dets = [{1: None, 2: None}] * 10 + [{}] * 5
    scores = {(i, tid): .8 for i in range(10) for tid in (1,2)}
    assert choose_speakers(dets, scores, {0}) == [None] * 15
    assert boolean_windows([False, True, True], fps=1) == [(1,3)]


def test_scene_cut_and_centered_single_face_switch_immediately():
    assert choose_speakers([{1: None}, {2: None}], {}, {0,1}) == [1,2]


def test_caption_pauses_are_not_highlighted_and_bottom_position(tmp_path):
    transcript = Transcript('pt', segments=[TranscriptSegment(0,0,3,'Olá mundo',[
        WordTimestamp('Olá',0,.2), WordTimestamp('mundo',2,2.4)])])
    output = tmp_path/'captions.ass'
    BrainRotCaptionBuilder(KineticCaptionStyle(short_phrase_max_words=4, animated=False)).build(transcript,output,0,3)
    events = pysubs2.load(str(output)).events
    assert len(events) == 2
    assert events[0].end == 200
    assert events[1].start == 2000
    assert r'\an2' in events[0].text
    assert r'\t(' not in events[0].text


def test_caption_offsets_and_clamping(tmp_path):
    t=Transcript('pt',segments=[TranscriptSegment(0,10,12,'teste', [WordTimestamp('teste',10.1,10.8)])])
    output=tmp_path/'c.ass'
    BrainRotCaptionBuilder().build(t,output,10.3,10.6)
    event=pysubs2.load(str(output)).events[0]
    assert (event.start,event.end)==(0,300)


def test_cuts_survive_compression_sampling_and_serialization():
    points=[TrackingPoint(i/25,100 if i<50 else 700,0,cut=i==50) for i in range(100)]
    compressed=ShortsRenderer._compress_points(points)
    sampled=ShortsRenderer._downsample_points(compressed,5)
    assert any(p.t==2 and p.cut for p in sampled)
    assert any(p.t==1.96 for p in sampled)
    path=TrackingPath(1920,1080,608,1080,points)
    assert TrackingPath.from_dict(path.to_dict()).points[50].cut
    expr=ShortsRenderer._build_coord_expr([TrackingPoint(0,100,0),TrackingPoint(2,700,0,cut=True)],'x',1000)
    assert '(700.000-100' not in expr
    assert 'gte(t,2.000)*700.000' in expr


def test_empty_subtitles_after_jump_cut_are_written(tmp_path):
    source=tmp_path/'original.ass';target=tmp_path/'cut.ass'
    sub=pysubs2.SSAFile();sub.events=[pysubs2.SSAEvent(start=1000,end=1500,text='deleted')];sub.save(str(source))
    renderer=object.__new__(ShortsRenderer)
    assert renderer._remap_subtitle_to_jump_cuts(source,target,[(0,.5),(2,3)])==0
    assert target.exists() and not pysubs2.load(str(target)).events


def test_many_jump_cuts_keep_sample_accurate_audio(tmp_path):
    # 20 cuts across AAC frame boundaries formerly accumulated audio drift.
    keep=[(i*.2,i*.2+.1) for i in range(20)]
    filters=[]
    video,audio=ShortsRenderer._append_jump_cut_filters(filters,keep,fps=30)
    output=tmp_path/'cuts.mkv'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
        'testsrc2=size=64x64:rate=30000/1001:duration=4', '-f','lavfi','-i',
        'sine=frequency=440:sample_rate=16000:duration=4',
        '-filter_complex',';'.join(filters).replace('[0:a]','[1:a]'),
        '-map',f'[{video}]','-map',f'[{audio}]','-c:v','ffv1','-c:a','pcm_s16le',str(output)],check=True)
    pcm=subprocess.run(['ffmpeg','-v','error','-i',str(output),'-vn','-f','s16le','-'],capture_output=True,check=True).stdout
    assert len(pcm)//2==32000
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v',
        '-show_entries','stream=nb_read_frames','-of','json',str(output)],capture_output=True,text=True,check=True).stdout)
    assert int(probe['streams'][0]['nb_read_frames'])==60


def test_local_defaults_do_not_spend_or_force_split():
    cfg=AppConfig()
    cfg.validate()
    assert cfg.curator_provider=='ollama'
    assert cfg.render_parallel_jobs==1
    assert cfg.force_single_layout
    assert not cfg.silence_jump_cuts_enabled
    assert cfg.whisper_alignment


def test_alignment_reuses_asr_and_releases_it_first(tmp_path, monkeypatch):
    from src.transcriber import WhisperTranscriber
    path=tmp_path/'a.wav';path.touch()
    worker=WhisperTranscriber(AppConfig(data_dir=tmp_path))
    calls=[]
    transcript=Transcript('pt',segments=[TranscriptSegment(0,0,1,'oi',[WordTimestamp('oi',0,.5)])])
    monkeypatch.setattr(worker,'_transcribe_once',lambda **kwargs: transcript)
    monkeypatch.setattr(worker,'unload_model',lambda: calls.append('unload'))
    def align(t,a):
        calls.append('align')
        return t
    monkeypatch.setattr(worker,'align_transcript',align)
    assert worker.transcribe(path) is transcript
    assert calls==['unload','align']


def test_short_speaker_flashes_are_suppressed():
    from src.active_speaker import stabilize_shots
    assert stabilize_shots([None]*25 + [1]*5 + [None]*25, {0}) == [None]*55
    assert stabilize_shots([1]*25 + [None]*5 + [1]*25, {0}) == [1]*55


def test_jump_tracking_injects_boundaries_and_preserves_crop():
    points=[TrackingPoint(0,0,0,w=400,h=700), TrackingPoint(10,100,0,w=400,h=700)]
    mapped=ShortsRenderer._remap_points_to_jump_cuts(points,[(0,1),(9,10)])
    assert [p.t for p in mapped]==[0,1,2]
    assert mapped[1].cut and mapped[1].x>95
    assert all(p.w==400 and p.h==700 for p in mapped)


def test_curator_covers_long_transcripts_without_omitting_text():
    from src.curator import OllamaCurator
    curator=OllamaCurator(AppConfig())
    transcript=Transcript('pt',segments=[TranscriptSegment(i,i*10,(i+1)*10,'palavra '*120) for i in range(400)])
    windows=curator._curation_windows(transcript)
    for seg in transcript.segments:
        assert any(start<=seg.start and end>=seg.end for start,end in windows)
    for start,end in windows:
        part=Transcript('pt',segments=[seg for seg in transcript.segments if seg.start>=start and seg.end<=end])
        assert '[...TRUNCATED...]' not in curator._build_prompt(part,3,start,end)
    assert curator._desired_clip_count(10800)==5


def test_cli_preserves_style_and_silence_override():
    from main import build_parser, apply_cli_overrides
    args=build_parser().parse_args(['--remove-silence'])
    cfg=AppConfig(captions_font_size=66,captions_short_phrase_max_words=3)
    result=apply_cli_overrides(cfg,args)
    assert result.captions_font_size==66 and result.captions_short_phrase_max_words==3
    assert result.silence_jump_cuts_enabled


def test_metadata_does_not_discard_good_clip_for_missing_buzzwords():
    from src.curator import OllamaCurator
    from src.models import ClipCandidate
    curator=OllamaCurator(AppConfig())
    transcript=Transcript('pt',segments=[TranscriptSegment(0,0,30,'Uma história com conclusão.')])
    clip=ClipCandidate('Uma história',0,30,reason='História completa',score=9)
    assert curator._enrich_clip_metadata([clip],transcript)[0].score==9


def test_curator_rejects_overlong_stories_instead_of_chopping_payoff():
    from src.curator import OllamaCurator
    from src.models import ClipCandidate
    curator=OllamaCurator(AppConfig(clip_max_seconds=60))
    assert not curator._post_process_candidates([ClipCandidate('História',0,120,score=9)],180,target_n=1)


def test_refinement_keeps_selected_hook_and_rejects_forced_padding():
    from src.curator import OllamaCurator
    from src.models import ClipCandidate
    curator=OllamaCurator(AppConfig(clip_min_seconds=20,clip_max_seconds=30))
    transcript=Transcript('pt',segments=[TranscriptSegment(0,0,10,'Contexto.'),TranscriptSegment(1,10,25,'Conclusão.')])
    assert curator._refine_for_hook_and_story([ClipCandidate('História',0,25,score=9)],transcript)[0].start==0
    assert not curator._refine_for_hook_and_story([ClipCandidate('Curto',10,25,score=9)],transcript)


def test_service_does_not_restore_rejected_duplicates():
    from src.pipeline import ShortsPipelineService
    from src.models import ClipCandidate
    service=object.__new__(ShortsPipelineService);service.config=AppConfig()
    chosen=service._select_clips([ClipCandidate('A',0,30,score=9),ClipCandidate('B',1,31,score=8)],2,100)
    assert len(chosen)==1


def test_failed_tracking_fallback_never_enables_arbitrary_center_crop(monkeypatch):
    renderer=object.__new__(ShortsRenderer);renderer.config=AppConfig()
    monkeypatch.setattr(renderer,'_probe_media_dimensions',lambda path:(1920,1080))
    path=renderer._build_center_tracking_path(Path('video.mp4'),0,30)
    assert path.fit_scene_mode and path.active_speaker_single_windows==[]


def test_render_reads_full_original_interval_after_silence_removal(tmp_path, monkeypatch):
    import src.renderer as module
    from src.models import ClipCandidate
    renderer=object.__new__(ShortsRenderer)
    renderer.config=AppConfig(silence_jump_cuts_enabled=True,temp_dir=tmp_path)
    monkeypatch.setattr(renderer,'_build_jump_keep_windows',lambda **kwargs:[(0,1),(9,10)])
    monkeypatch.setattr(renderer,'_warn_muxed_av_duration_mismatch',lambda **kwargs:None)
    monkeypatch.setattr(module,'release_gpu_memory',lambda:None)
    commands=[]
    monkeypatch.setattr(module,'run_cmd',lambda cmd,**kw:commands.append(cmd))
    path=TrackingPath(640,360,200,360,[TrackingPoint(0,0,0),TrackingPoint(10,0,0)])
    renderer._run_ffmpeg_native_vertical(tmp_path/'in.mp4',ClipCandidate('test',20,30),
        tmp_path/'out.mp4',None,path,Transcript('pt'))
    cmd=commands[-1]
    durations=[cmd[i+1] for i,arg in enumerate(cmd) if arg=='-t']
    assert durations==['10.000','2.000']


def test_only_detected_listener_in_wide_shot_is_not_assumed_speaker():
    dets=[{1:np.array([100,100,60,80])}]*25
    assert choose_speakers(dets,{}, {0},frame_width=960)==[None]*25
    scores={(i,1):.95 for i in range(25)}
    assert choose_speakers(dets,scores,{0},frame_width=960)==[1]*25


def test_invalid_llm_durations_get_one_repair_without_cutting_story(monkeypatch):
    from src.curator import OllamaCurator
    curator=OllamaCurator(AppConfig(clip_min_seconds=20,clip_max_seconds=60,ollama_clips_count=1))
    transcript=Transcript('pt',segments=[TranscriptSegment(i,i*10,(i+1)*10,'História completa.') for i in range(12)])
    invalid={'clips':[{'title':'Longo','reason':'História completa','start':0,'end':120,'score':9,'layout_mode':'single'}]}
    repaired={'clips':[{'title':'Válido','reason':'Outra história completa','start':10,'end':40,'score':9,'layout_mode':'single'}]}
    calls=[]
    def generate(prompt):
        calls.append(prompt)
        return json.dumps(invalid if len(calls)==1 else repaired)
    monkeypatch.setattr(curator,'_call_ollama_generate',generate)
    result=curator.curate(transcript)
    assert len(calls)==2 and 'violou a duração' in calls[1]
    assert result.parsed_ok and [(c.start,c.end) for c in result.clips]==[(10,40)]


def test_alignment_accepts_whisperx_splitting_a_segment(monkeypatch):
    import whisperx
    from src.transcriber import WhisperTranscriber
    monkeypatch.setattr(whisperx,'load_align_model',lambda **kw:(object(),{}))
    monkeypatch.setattr(whisperx,'load_audio',lambda path:np.zeros(32000))
    monkeypatch.setattr(whisperx,'align',lambda *args,**kw:{'segments':[
        {'words':[{'word':'Olá.','start':.1,'end':.4}]},
        {'words':[{'word':'Tchau.','start':1.1,'end':1.5}]}]})
    transcript=Transcript('pt',segments=[TranscriptSegment(0,0,2,'Olá. Tchau.',[
        WordTimestamp('Olá.',0,.5),WordTimestamp('Tchau.',1,1.8)])])
    result=WhisperTranscriber(AppConfig(whisper_device='cpu')).align_transcript(transcript,Path('audio.wav'))
    assert result.timing_source=='whisperx'
    assert [w.start for w in result.words]==[.1,1.1]
