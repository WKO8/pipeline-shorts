from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.native_subtitles_extractor import (
    _parse_vtt_text,
    extract_native_subtitles,
    native_segments_to_transcript,
)


def test_parse_vtt_text():
    sample_vtt = """WEBVTT
Kind: captions
Language: pt

00:00:01.500 --> 00:00:04.200
<c>Olá</c> pessoal, bem-vindos [música]

00:00:04.500 --> 00:00:08.000
ao nosso podcast de <b>tecnologia</b>.
"""
    segments = _parse_vtt_text(sample_vtt)
    assert len(segments) == 2
    assert segments[0]["start"] == 1.5
    assert segments[0]["end"] == 4.2
    assert segments[0]["text"] == "Olá pessoal, bem-vindos"
    assert segments[1]["start"] == 4.5
    assert segments[1]["end"] == 8.0
    assert segments[1]["text"] == "ao nosso podcast de tecnologia."


def test_extract_native_subtitles_priority():
    fake_info = {
        "id": "vid_xyz",
        "title": "Podcast Teste",
        "duration": 1200,
        "uploader": "Test Channel",
        "webpage_url": "https://youtube.com/watch?v=vid_xyz",
        "subtitles": {
            "en": [{"ext": "vtt", "url": "http://example.com/en_manual.vtt"}]
        },
        "automatic_captions": {
            "pt-orig": [{"ext": "vtt", "url": "http://example.com/pt_orig.vtt"}],
            "pt": [{"ext": "vtt", "url": "http://example.com/pt_auto.vtt"}],
        },
    }

    mock_vtt_content = """WEBVTT

00:00:00.000 --> 00:00:05.000
Fala galera, estamos ao vivo!
""".encode("utf-8")

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls, \
         patch("urllib.request.urlopen") as mock_urlopen:

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = fake_info
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = mock_vtt_content
        mock_urlopen.return_value.__enter__.return_value = mock_response

        segments, metadata = extract_native_subtitles(
            url="https://youtube.com/watch?v=vid_xyz",
            preferred_langs=["pt-BR", "pt", "en"],
        )

        assert metadata["id"] == "vid_xyz"
        assert segments is not None
        assert len(segments) == 1
        assert segments[0]["text"] == "Fala galera, estamos ao vivo!"


def test_native_segments_to_transcript():
    sample_segments = [
        {"start": 10.0, "end": 14.0, "text": "Inteligência artificial generativa"},
        {"start": 14.5, "end": 18.0, "text": "mudou completamente o cenário."},
    ]

    transcript = native_segments_to_transcript(sample_segments, language="pt")
    assert transcript.language == "pt"
    assert transcript.timing_source == "native_vtt"
    assert len(transcript.segments) == 2
    assert transcript.segments[0].start == 10.0
    assert transcript.segments[0].end == 14.0
    assert len(transcript.segments[0].words) == 3
    assert transcript.segments[0].words[0].text == "Inteligência"
    assert transcript.full_text == "Inteligência artificial generativa mudou completamente o cenário."


def test_slice_segments_for_cut():
    from src.native_subtitles_extractor import slice_segments_for_cut

    sample_segments = [
        {"start": 100.0, "end": 105.0, "text": "Antes do corte."},
        {"start": 120.0, "end": 125.0, "text": "Eu comecei pagando três mil."},
        {"start": 125.5, "end": 130.0, "text": "Hoje eu cobro duzentos mil."},
        {"start": 135.0, "end": 140.0, "text": "Depois do corte."},
    ]

    # Slice between 120.0s and 130.0s
    sliced = slice_segments_for_cut(sample_segments, cut_start=120.0, cut_end=130.0)
    assert len(sliced.segments) == 2
    # Timestamps are shifted to start at 0.0s!
    assert sliced.segments[0].start == 0.0
    assert sliced.segments[0].end == 5.0
    assert sliced.segments[0].text == "Eu comecei pagando três mil."
    assert sliced.segments[1].start == 5.5
    assert sliced.segments[1].end == 10.0
    assert sliced.segments[1].text == "Hoje eu cobro duzentos mil."
    assert sliced.timing_source == "native_vtt_sliced"
