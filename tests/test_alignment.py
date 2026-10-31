from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pytest

from src.config import AppConfig, load_config
from src.models import Transcript, TranscriptSegment, WordTimestamp
from src.transcriber import WhisperTranscriber


def install_aligner(monkeypatch, align):
    monkeypatch.setitem(sys.modules, 'whisperx', SimpleNamespace(
        load_align_model=lambda **kw: (object(), {}),
        load_audio=lambda path: np.zeros(64000), align=align))
    monkeypatch.setitem(sys.modules, 'torch', SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setattr('src.transcriber.release_gpu_memory', lambda: None)


def transcript():
    return Transcript('pt', timing_source='faster-whisper', segments=[
        TranscriptSegment(0, 1, 2, 'Olá mundo', [
            WordTimestamp('Olá', 1, 1.3), WordTimestamp('mundo', 1.4, 2)])])


@pytest.mark.parametrize('raw', [
    [{'word': 'Olá', 'start': 1, 'end': 1.2}],
    [{'word': 'Olá', 'start': float('nan'), 'end': 1.2}, {'word': 'mundo', 'start': 1.3, 'end': 2}],
    [{'word': 'Olá', 'start': 1, 'end': 1.6}, {'word': 'mundo', 'start': 1.5, 'end': 2}],
    [{'word': 'Olá', 'start': .5, 'end': 1.2}, {'word': 'mundo', 'start': 1.3, 'end': 2}],
])
def test_invalid_alignment_preserves_all_original_words(monkeypatch, raw):
    install_aligner(monkeypatch, lambda *a, **kw: {'segments': [{'words': raw}]})
    t = transcript(); before = t.to_dict()
    WhisperTranscriber(AppConfig(whisper_alignment_retry_padding=0)).align_transcript(t, Path('x.wav'))
    assert t.to_dict() == before


def test_retry_recovers_only_failed_segment_and_respects_audio_bounds(monkeypatch):
    calls = []
    def align(segments, *a, **kw):
        calls.append(segments[0])
        raw = [] if len(calls) == 1 else [
            {'word': 'Olá', 'start': .9, 'end': 1.2},
            {'word': 'mundo', 'start': 1.4, 'end': 2.1}]
        return {'segments': [{'words': raw}]}
    install_aligner(monkeypatch, align)
    t = transcript()
    WhisperTranscriber(AppConfig()).align_transcript(t, Path('x.wav'))
    assert [(c['start'], c['end']) for c in calls] == [(1, 2), (.75, 2.25)]
    assert t.timing_source == 'whisperx'
    assert (t.start, t.end) == (.9, 2.1)


def test_no_retry_borrows_adjacent_speech(monkeypatch):
    calls = []
    def align(segments, *a, **kw):
        calls.append(segments[0]); return {'segments': []}
    install_aligner(monkeypatch, align)
    t = transcript()
    t.segments.insert(0, TranscriptSegment(2, 0, 1, 'antes'))
    t.segments.append(TranscriptSegment(3, 2, 4, 'depois'))
    WhisperTranscriber(AppConfig()).align_transcript(t, Path('x.wav'))
    assert len(calls) == 3
    assert [(c['start'], c['end']) for c in calls] == [(0, 1), (1, 2), (2, 4)]
    assert t.timing_source == 'faster-whisper'


def test_partial_word_fallback_is_reported(monkeypatch):
    install_aligner(monkeypatch, lambda *a, **kw: {'segments': [{'words': [
        {'word': 'Olá', 'start': 1.1, 'end': 1.3}, {'word': 'mundo'}]}]})
    t = transcript()
    WhisperTranscriber(AppConfig()).align_transcript(t, Path('x.wav'))
    assert t.timing_source == 'whisperx-partial'
    assert t.words[1].start == 1.4


def test_optional_alignment_load_failure_keeps_asr(monkeypatch):
    def fail(**kw):
        raise RuntimeError('missing model')
    install_aligner(monkeypatch, None)
    monkeypatch.setattr(sys.modules['whisperx'], 'load_align_model', fail)
    t = transcript(); before = t.to_dict()
    WhisperTranscriber(AppConfig()).align_transcript(t, Path('x.wav'))
    assert t.to_dict() == before


def test_env_quotes_comments_and_silence_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv('SILENCE_MIN_DURATION_SECONDS', raising=False)
    monkeypatch.delenv('SILENCE_NOISE_DB', raising=False)
    (tmp_path / '.env').write_text('WHISPER_MODEL="large-v3-turbo" # comment\nCAPTIONS_COLOR=#FFFFFF\nWHISPER_ALIGNMENT_RETRY_PADDING=0.5\n')
    cfg = load_config(project_root=tmp_path)
    assert cfg.whisper_model == 'large-v3-turbo'
    assert cfg.captions_color == '#FFFFFF'
    assert cfg.whisper_alignment_retry_padding == .5
    assert cfg.silence_min_duration_seconds == AppConfig().silence_min_duration_seconds == .8
    assert cfg.silence_noise_db == AppConfig().silence_noise_db == -40


def test_interruption_preserves_completed_alignment_and_marks_partial(monkeypatch):
    calls = []
    def align(*a, **kw):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError('out of memory')
        return {'segments': [{'words': [
            {'word': 'Olá', 'start': 1.1, 'end': 1.3},
            {'word': 'mundo', 'start': 1.4, 'end': 1.9}]}]}
    install_aligner(monkeypatch, align)
    t = transcript()
    t.segments.append(TranscriptSegment(1, 2, 3, 'fim', [WordTimestamp('fim', 2, 3)]))
    WhisperTranscriber(AppConfig()).align_transcript(t, Path('x.wav'))
    assert t.timing_source == 'whisperx-partial'
    assert t.words[0].start == 1.1
    assert t.words[-1].start == 2


def test_ctc_frame_rounding_is_clamped_instead_of_rejected():
    segment = transcript().segments[0]
    words, mixed = WhisperTranscriber._alignment_words(segment, {'segments': [{'words': [
        {'word': 'Olá', 'start': 1, 'end': 1.3},
        {'word': 'mundo', 'start': 1.4, 'end': 2.021}]}]}, 1, 2)
    assert words[-1].end == 2
    assert not mixed
