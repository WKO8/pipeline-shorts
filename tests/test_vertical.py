import json
import shutil
from pathlib import Path

import pytest
import pysubs2

from src.captions import BrainRotCaptionBuilder, CaptionPlacement, KineticCaptionStyle
from src.config import AppConfig
from src.models import Transcript, TranscriptSegment, WordTimestamp
from src.vertical import (VerticalOptions, composition_filter, download_social, execute,
                          ffmpeg_render, prepare_base, probe, speech_ranges, validate_social_url)


def test_silence_edges_and_all_silent():
    assert speech_ranges('silence_start: 0\nsilence_end: 2', 4, 30) == [(1.9333333333333333, 4)]
    assert speech_ranges('silence_start: 2', 4, 30) == [(0, 2.066666666666667)]
    assert speech_ranges('silence_start: 0', 4, 30) == []
    assert speech_ranges('', 4, 60) == [(0, 4)]


@pytest.mark.parametrize('url', ['file:///tmp/v.mp4', 'https://youtube.com.evil.test/x',
                                      'https://nobody@youtube.com/x', 'http://localhost/x'])
def test_url_validation(url):
    with pytest.raises(ValueError):
        validate_social_url(url)


def test_headers_fallback_and_best_quality(tmp_path, monkeypatch):
    calls = []
    def fake(cmd):
        calls.append(cmd)
        if len(calls) == 1:
            raise RuntimeError('403')
        source = tmp_path / 'source.webm'
        source.touch()
        (tmp_path / 'download-path.txt').write_text(str(source))
    monkeypatch.setattr('src.vertical.execute', fake)
    monkeypatch.setattr('src.vertical.probe', lambda path: {})
    assert download_social('https://www.tiktok.com/@test/video/1', tmp_path, AppConfig()).suffix == '.webm'
    assert '--add-headers' not in calls[0] and '--add-headers' in calls[1]
    assert calls[0][calls[0].index('-f')+1] == 'bestvideo*+bestaudio/best'


def test_caption_placement_is_opt_in(tmp_path):
    transcript = Transcript('pt', segments=[TranscriptSegment(0, 0, .5, 'Teste', [WordTimestamp('Teste', 0, .5)])])
    builder = BrainRotCaptionBuilder(KineticCaptionStyle())
    old = builder.build(transcript, tmp_path/'old.ass', 0, 1)
    new = builder.build(transcript, tmp_path/'new.ass', 0, 1, placement=CaptionPlacement(100, True))
    assert r'\pos' not in old.ass_path.read_text()
    text = new.ass_path.read_text()
    assert r'\clip(54,154,918,1536)' in text
    assert r'\pos(486,1404)' in text
    assert pysubs2.load(str(new.srt_path))[0].text == 'TESTE'


@pytest.fixture
def media(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('FFmpeg required')
    base = tmp_path/'base.mkv'
    # Two 1 s tones separated by 1 s silence. Distinct pictures expose cuts/layout errors.
    execute(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=180x320:r=30:d=3',
             '-f', 'lavfi', '-i', r"aevalsrc=if(between(t\,1\,2)\,0\,0.2*sin(2*PI*440*t)):s=48000:d=3",
             '-c:v', 'libx264', '-threads', '2', '-c:a', 'pcm_s16le', str(base)])
    extra = tmp_path/'extra.mp4'
    execute(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=160x90:r=24:d=0.4',
             '-c:v', 'libx264', '-threads', '2', str(extra)])
    return base, extra


def test_real_jump_cuts_preserve_sync(media, tmp_path):
    base, _ = media
    edited, ranges = prepare_base(base, tmp_path, VerticalOptions(remove_silence=True, captions=False))
    assert len(ranges) == 2
    duration = sum(b-a for a, b in ranges)
    assert 2.0 < duration < 2.2
    info = probe(edited)
    assert abs(info['duration'] - duration) < 1/30
    raw = execute(['ffmpeg', '-v', 'error', '-i', str(edited), '-af', 'astats', '-f', 'null', '-'])
    assert raw.returncode == 0
    # PCM sample count must match frame-grid duration, including crossfade joins.
    wav = tmp_path/'cut.wav'
    execute(['ffmpeg', '-v', 'error', '-i', str(edited), '-vn', '-ac', '1', str(wav)])
    import wave
    with wave.open(str(wav)) as f:
        assert abs(f.getnframes()/f.getframerate() - duration) < 1/48000


@pytest.mark.parametrize('layout,fps,invert', [('full_screen',30,False),
    ('split_vertical_top_bottom',30,True), ('split_side_by_side',60,False), ('pip_overlay',30,False)])
def test_real_layouts(media, tmp_path, layout, fps, invert):
    base, extra = media
    options = VerticalOptions(layout=layout, fps=fps, invert=invert, captions=False)
    graph = composition_filter(options, .8, True, False)
    output = tmp_path/'out.mp4'
    ffmpeg_render(['-i', str(base), '-stream_loop', '-1', '-i', str(extra)], graph, output, .8, fps)
    info = probe(output)
    assert (info['width'], info['height']) == (1080,1920)
    assert abs(info['duration']-.8) < 1/fps
    streams = json.loads(execute(['ffprobe','-v','error','-show_streams','-of','json',str(output)]).stdout)['streams']
    assert streams[0]['avg_frame_rate'] == f'{fps}/1'
    assert abs(float(streams[0]['duration']) - float(streams[1]['duration'])) < 1/fps


def test_api_rejects_paths_and_bad_options():
    from ui.app import app
    client = app.test_client()
    base = {'base':'https://youtube.com/shorts/123', 'secondary_mode':'preset', 'secondary':'../../.env'}
    assert client.post('/api/vertical/run', json=base).status_code == 400
    assert client.post('/api/vertical/run', json={'base':base['base'], 'options':{'fps':24}}).status_code == 400
    assert client.get('/api/vertical/presets').status_code == 200


def test_pipeline_transcribes_only_edited_base(media, tmp_path, monkeypatch):
    from src.vertical import run_vertical
    import wave
    base, extra = media
    seen = []
    def transcribe(self, path, output_json_path):
        with wave.open(str(path)) as f:
            seen.append(f.getnframes() / f.getframerate())
        return Transcript('pt', segments=[TranscriptSegment(0, 0, .5, 'Olá', [WordTimestamp('Olá', 0, .5)])])
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.transcribe', transcribe)
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.unload_model', lambda self: None)
    output = run_vertical(base, extra, VerticalOptions(layout='split_vertical_top_bottom', remove_silence=True),
                          AppConfig(), tmp_path/'pipeline')
    assert 2 < seen[0] < 2.2
    assert abs(probe(output)['duration']-seen[0]) < 1/30
    assert output.with_name('captions.srt').exists()
    assert 'ass=captions.ass' in output.with_suffix('.filter.txt').read_text()


def test_audio_mix_and_missing_base_audio(media, tmp_path, monkeypatch):
    from src.vertical import run_vertical
    def forbidden(*args, **kwargs):
        pytest.fail('Whisper should not run when captions are disabled')
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.transcribe', forbidden)
    base, silent_video = media
    output = run_vertical(silent_video, base,
        VerticalOptions(layout='pip_overlay', secondary_volume=.5, captions=False, remove_silence=True),
        AppConfig(), tmp_path/'mixed')
    assert probe(output)['audio']
    assert not output.with_name('captions.ass').exists()
    assert 'volume=0.5' in output.with_suffix('.filter.txt').read_text()
    # The complementary stream is longer than the base and must be truncated.
    assert abs(probe(output)['duration']-probe(silent_video)['duration']) <= 1/30


def test_subtitles_only_mode(media, tmp_path, monkeypatch):
    from src.vertical import run_vertical
    base, _ = media
    def fake_transcribe(self, path, output_json_path):
        return Transcript('pt', segments=[TranscriptSegment(0, 0, .5, 'Teste', [WordTimestamp('Teste', 0, .5)])])
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.transcribe', fake_transcribe)
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.unload_model', lambda self: None)

    options = VerticalOptions(subtitles_only=True, caption_y=70, safe_zones=True)
    options.validate()
    assert options.subtitles_only is True

    # Graph should be full_screen and burning captions
    graph = composition_filter(options, 3.0, secondary=False, secondary_audio=False, ass=True)
    assert 'ass=captions.ass' in graph
    assert '1080:1920' in graph

    output = run_vertical(base, None, options, AppConfig(), tmp_path/'subsonly')
    assert output.exists()
    assert output.with_name('captions.srt').exists()
    assert output.with_name('captions.ass').exists()


def test_frame_preview_endpoint(media):
    from ui.app import app
    base, _ = media
    client = app.test_client()

    # Missing source parameter returns 400
    res = client.get('/api/vertical/frame_preview')
    assert res.status_code == 400

    # Local file source returns 200 with image/jpeg
    res = client.get(f'/api/vertical/frame_preview?source={base}&timestamp=1.0')
    assert res.status_code == 200
    assert res.content_type == 'image/jpeg'
    assert len(res.data) > 0


def test_caption_styling_and_uppercase_customization(media, tmp_path, monkeypatch):
    from src.vertical import run_vertical
    base, _ = media

    def fake_transcribe(self, path, output_json_path):
        return Transcript('pt', segments=[
            TranscriptSegment(0, 0, 1.0, 'minha frase de teste', [
                WordTimestamp('minha', 0, .25),
                WordTimestamp('frase', .25, .5),
                WordTimestamp('de', .5, .75),
                WordTimestamp('teste', .75, 1.0)
            ])
        ])

    monkeypatch.setattr('src.transcriber.WhisperTranscriber.transcribe', fake_transcribe)
    monkeypatch.setattr('src.transcriber.WhisperTranscriber.unload_model', lambda self: None)

    # 1. Custom font size 75 + FORCE UPPERCASE = True
    opt_upper = VerticalOptions(
        subtitles_only=True,
        caption_font_size=75,
        caption_force_uppercase=True,
        caption_color='#00FF00',
        caption_stroke_width=5.0
    )
    opt_upper.validate()
    out_upper = run_vertical(base, None, opt_upper, AppConfig(), tmp_path/'upper_test')
    ass_upper = out_upper.with_name('captions.ass').read_text()
    srt_upper = pysubs2.load(str(out_upper.with_name('captions.srt')))

    assert '75' in ass_upper  # font size 75 in style
    assert 'MINHA' in ass_upper
    assert 'FRASE' in ass_upper
    assert 'TESTE' in ass_upper
    assert 'minha' not in srt_upper[0].text
    assert 'MINHA' in srt_upper[0].text

    # 2. Custom font size 55 + FORCE UPPERCASE = False (preserves original case)
    opt_lower = VerticalOptions(
        subtitles_only=True,
        caption_font_size=55,
        caption_force_uppercase=False,
        caption_color='#FF00FF',
        caption_stroke_width=2.0
    )
    opt_lower.validate()
    out_lower = run_vertical(base, None, opt_lower, AppConfig(), tmp_path/'lower_test')
    ass_lower = out_lower.with_name('captions.ass').read_text()
    srt_lower = pysubs2.load(str(out_lower.with_name('captions.srt')))

    assert '55' in ass_lower  # font size 55 in style
    assert 'minha' in ass_lower
    assert 'MINHA' not in ass_lower
    assert 'minha' in srt_lower[0].text

    # 3. Validation limits
    with pytest.raises(ValueError):
        VerticalOptions(caption_font_size=5).validate()
    with pytest.raises(ValueError):
        VerticalOptions(caption_font_size=500).validate()
    with pytest.raises(ValueError):
        VerticalOptions(caption_force_uppercase='not_a_bool').validate()


