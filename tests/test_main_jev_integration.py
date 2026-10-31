from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from main import build_parser, apply_cli_overrides, _run_discover_standalone
from src.config import AppConfig


def test_build_parser_includes_new_jev_flags():
    parser = build_parser()
    args = parser.parse_args([
        "--discover", "podcast tech",
        "--discover-limit", "10",
        "--discover-min-score", "0.75",
        "--auto-pipeline",
        "--jev-curate",
        "--http-range",
    ])
    assert args.discover == "podcast tech"
    assert args.discover_limit == 10
    assert args.discover_min_score == 0.75
    assert args.auto_pipeline is True
    assert args.jev_curate is True
    assert args.http_range is True


def test_apply_cli_overrides_sets_jev_enabled():
    cfg = AppConfig()
    parser = build_parser()
    args = parser.parse_args(["--jev-curate"])
    new_cfg = apply_cli_overrides(cfg, args)
    assert new_cfg.jev_enabled is True


def test_curator_jev_cli_flag_and_adapter():
    from src.curator import get_curator, JevCuratorAdapter
    cfg = AppConfig()
    parser = build_parser()
    args = parser.parse_args(["--curator", "jev"])
    assert args.curator == "jev"
    new_cfg = apply_cli_overrides(cfg, args)
    assert new_cfg.curator_provider == "jev"
    assert new_cfg.jev_enabled is True
    assert getattr(args, "jev_curate", False) is True
    curator_inst = get_curator(new_cfg)
    assert isinstance(curator_inst, JevCuratorAdapter)


def test_run_discover_standalone(tmp_path: Path):
    cfg = AppConfig(data_dir=tmp_path)
    parser = build_parser()
    args = parser.parse_args(["--discover", "inteligencia artificial", "--discover-min-score", "0.5"])

    fake_candidates = [
        {
            "rank": 1,
            "video_id": "vid123",
            "url": "https://youtube.com/watch?v=vid123",
            "title": "A Revolução da IA",
            "channel": "Canal Tech",
            "duration_seconds": 3600,
            "duration_formatted": "1h 0m",
            "view_count": 50000,
            "jev_score": 0.88,
            "jev_viral_score": 0.85,
            "jev_topic_relevance": 0.92,
        }
    ]

    with patch("src.youtube_discovery.YouTubeDiscoveryScraper.discover_and_rank") as mock_disc:
        mock_disc.return_value = fake_candidates
        ret = _run_discover_standalone(cfg, args)
        assert ret == 0
        expected_json = tmp_path / "curated_videos_inteligencia_artificial.json"
        assert expected_json.exists()
        saved = json.loads(expected_json.read_text(encoding="utf-8"))
        assert saved["metadata"]["total_curated_kept"] == 1
        assert saved["curated_videos"][0]["video_id"] == "vid123"


def test_run_http_range_pipeline_fallback_whisper_uses_which(tmp_path: Path):
    from main import _run_http_range_pipeline
    from src.models import Transcript, TranscriptSegment
    cfg = AppConfig(data_dir=tmp_path, audio_dir=tmp_path / "audio", downloads_dir=tmp_path / "downloads")
    cfg.ensure_directories()
    parser = build_parser()
    args = parser.parse_args(["--url", "https://www.youtube.com/watch?v=mock123", "--http-range", "--dry-run"])

    mock_transcript = Transcript(
        language="pt",
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="Olá mundo")],
    )

    with patch("main.extract_native_subtitles", return_value=([], {})), \
         patch("main.run_cmd") as mock_run_cmd, \
         patch("main.transcribe_audio", return_value=mock_transcript), \
         patch("src.jev_curator.JevTwoLayerCurator.curate", return_value=[]):
        ret = _run_http_range_pipeline(cfg, args, slug="mock123", url=args.url)
        assert ret == 0
        assert mock_run_cmd.called
        cmd_called = mock_run_cmd.call_args[0][0]
        assert any("yt-dlp" in part for part in cmd_called)


def test_run_http_range_pipeline_streaming_transcribes_with_whisperx(tmp_path: Path):
    from main import _run_http_range_pipeline
    from src.models import Transcript, TranscriptSegment
    cfg = AppConfig(data_dir=tmp_path, audio_dir=tmp_path / "audio", downloads_dir=tmp_path / "downloads")
    cfg.ensure_directories()
    parser = build_parser()
    args = parser.parse_args(["--url", "https://www.youtube.com/watch?v=mock123", "--http-range"])

    mock_transcript = Transcript(
        language="pt",
        segments=[TranscriptSegment(id=0, start=0.0, end=40.0, text="Texto transcrito com WhisperX")],
    )

    fake_cuts = [
        {
            "cut_id": 1,
            "start_second": 1260.0,
            "end_second": 1338.6,
            "duration_seconds": 78.6,
            "hook_score": 0.69,
            "narrative_score": 0.82,
            "combined_score": 0.768,
            "video_file": str(tmp_path / "downloads" / "corte_01.mp4"),
        }
    ]

    # Create dummy video file
    dummy_video = tmp_path / "downloads" / "corte_01.mp4"
    dummy_video.write_bytes(b"dummy video data")

    from src.renderer import RenderResult
    from src.models import ClipCandidate

    mock_render_result = RenderResult(
        clip=ClipCandidate(start=0.0, end=78.6, title="corte_01"),
        output_path=str(tmp_path / "corte_01_vertical.mp4"),
    )

    with patch("main.extract_native_subtitles", return_value=([{"start": 0.0, "end": 10.0, "text": "intro"}], {})), \
         patch("src.jev_curator.JevTwoLayerCurator.curate", return_value=fake_cuts), \
         patch("src.range_downloader.SelectiveRangeDownloader.download_cuts_stream", return_value=fake_cuts), \
         patch("main.run_cmd") as mock_run_cmd, \
         patch("main.transcribe_audio", return_value=mock_transcript) as mock_transcribe, \
         patch("src.renderer.ShortsRenderer.render_single_clip", return_value=mock_render_result) as mock_render:
        ret = _run_http_range_pipeline(cfg, args, slug="mock123", url=args.url)
        assert ret == 0
        assert mock_transcribe.called
        # Verify transcribe_audio was invoked with defer_alignment=False
        kwargs = mock_transcribe.call_args[1]
        assert kwargs.get("defer_alignment") is False
        # Verify clip candidate passed to renderer has start=0.0 and NO source_spans (avoiding Montage mode)
        render_clip = mock_render.call_args[1]["clip"]
        assert render_clip.start == 0.0
        assert len(render_clip.source_spans) == 0
        # Verify rendered result clip has original timestamps for manifest/reporting
        assert mock_render_result.clip.start == 1260.0
        assert mock_render_result.clip.end == 1338.6


