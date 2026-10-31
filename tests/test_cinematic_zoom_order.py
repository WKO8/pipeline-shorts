from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import AppConfig
from src.models import ClipCandidate, Transcript, TranscriptSegment, TrackingPath, TrackingPoint
from src.renderer import ShortsRenderer


def test_cinematic_zoom_applied_before_subtitles(tmp_path: Path):
    cfg = AppConfig(
        cinematic_zoom=True,
        captions_burn_in_video=True,
        clips_dir=tmp_path / "clips",
        temp_dir=tmp_path / "temp",
        subtitles_dir=tmp_path / "subs",
    )
    cfg.ensure_directories()
    renderer = ShortsRenderer(cfg)

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"dummy" * 50)

    transcript = Transcript(
        language="pt",
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="Teste de legenda")],
    )
    clip = ClipCandidate(
        title="Clip 1",
        start=0.0,
        end=10.0,
        score=0.9,
        reason="Viral",
    )

    tracking = TrackingPath(
        source_width=1920,
        source_height=1080,
        crop_width=608,
        crop_height=1080,
        points=[TrackingPoint(t=0.0, x=500.0, y=0.0, fx=0.5, fy=0.3)],
    )

    sub_file = tmp_path / "subs" / "test.ass"
    sub_file.parent.mkdir(parents=True, exist_ok=True)
    sub_file.write_text("Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,Legenda teste\n")

    call_order = []

    def mock_run_ffmpeg(*args, **kwargs):
        call_order.append("vertical_render_raw")
        assert kwargs.get("burn_subtitles") is False
        output_p = kwargs.get("output_path")
        output_p.write_bytes(b"dummy_raw" * 50)
        return sub_file

    def mock_apply_zoom(input_video, output_video, tracking_points=None, eye_line_ratio=0.30):
        call_order.append("apply_cinematic_zoom")
        assert "_raw" in str(input_video)
        output_video.write_bytes(b"dummy_zoomed" * 50)

    def mock_run_cmd(cmd, check=True):
        cmd_str = " ".join(str(x) for x in cmd)
        if "subtitles=" in cmd_str:
            call_order.append("burn_subtitles_on_zoomed")
            assert "_zoomed" in cmd_str
            out_file = Path(cmd[-1])
            out_file.write_bytes(b"dummy_final" * 50)
        elif "_video.mp4" in cmd_str:
            call_order.append("extract_modular")
            out_file = Path(cmd[-5]) if "-an" in cmd else Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(b"dummy_modular" * 10)

    with patch.object(renderer, "_probe_media_duration", return_value=10.0), \
         patch.object(renderer, "_ensure_nvenc_encoder"), \
         patch.object(renderer, "_run_ffmpeg_native_vertical", side_effect=mock_run_ffmpeg), \
         patch("src.post_processor.apply_cinematic_zoom", side_effect=mock_apply_zoom), \
         patch("src.renderer.run_cmd", side_effect=mock_run_cmd):

        res = renderer.render_single_clip(
            source_video=source_video,
            transcript=transcript,
            clip=clip,
            output_dir=tmp_path / "clips",
            slug="video",
            index=1,
            subtitle_override_path=sub_file,
            tracking_override=tracking,
        )

        assert res.zoom_applied is True
        assert "vertical_render_raw" in call_order
        assert "apply_cinematic_zoom" in call_order
        assert "burn_subtitles_on_zoomed" in call_order

        idx_raw = call_order.index("vertical_render_raw")
        idx_zoom = call_order.index("apply_cinematic_zoom")
        idx_sub = call_order.index("burn_subtitles_on_zoomed")

        assert idx_raw < idx_zoom < idx_sub, f"Invalid order: {call_order}"


def test_modular_extracted_clean_without_zoom(tmp_path: Path):
    cfg = AppConfig(
        cinematic_zoom=False,
        captions_burn_in_video=True,
        clips_dir=tmp_path / "clips",
        temp_dir=tmp_path / "temp",
        subtitles_dir=tmp_path / "subs",
    )
    cfg.ensure_directories()
    renderer = ShortsRenderer(cfg)

    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"dummy" * 50)

    transcript = Transcript(
        language="pt",
        segments=[TranscriptSegment(id=0, start=0.0, end=10.0, text="Teste de legenda")],
    )
    clip = ClipCandidate(title="Clip 1", start=0.0, end=10.0, score=0.9, reason="Viral")
    tracking = TrackingPath(
        source_width=1920, source_height=1080, crop_width=608, crop_height=1080,
        points=[TrackingPoint(t=0.0, x=500.0, y=0.0, fx=0.5, fy=0.3)],
    )

    sub_file = tmp_path / "subs" / "test.ass"
    sub_file.parent.mkdir(parents=True, exist_ok=True)
    sub_file.write_text("Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,Legenda teste\n")

    call_order = []
    extracted_from_file = []

    def mock_run_ffmpeg(*args, **kwargs):
        call_order.append("vertical_render_raw")
        assert kwargs.get("burn_subtitles") is False
        output_p = kwargs.get("output_path")
        output_p.write_bytes(b"dummy_raw" * 50)
        return sub_file

    def mock_run_cmd(cmd, check=True):
        cmd_str = " ".join(str(x) for x in cmd)
        if "subtitles=" in cmd_str:
            call_order.append("burn_subtitles_on_raw")
            out_file = Path(cmd[-1])
            out_file.write_bytes(b"dummy_final" * 50)
        elif "_video.mp4" in cmd_str:
            call_order.append("extract_modular")
            input_file = cmd[cmd.index("-i") + 1]
            extracted_from_file.append(str(input_file))
            out_file = Path(cmd[-5]) if "-an" in cmd else Path(cmd[-1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_bytes(b"dummy_modular" * 10)

    with patch.object(renderer, "_probe_media_duration", return_value=10.0), \
         patch.object(renderer, "_ensure_nvenc_encoder"), \
         patch.object(renderer, "_run_ffmpeg_native_vertical", side_effect=mock_run_ffmpeg), \
         patch("src.renderer.run_cmd", side_effect=mock_run_cmd):

        res = renderer.render_single_clip(
            source_video=source_video,
            transcript=transcript,
            clip=clip,
            output_dir=tmp_path / "clips",
            slug="video",
            index=1,
            subtitle_override_path=sub_file,
            tracking_override=tracking,
        )

        assert res.zoom_applied is False
        assert "vertical_render_raw" in call_order
        assert "extract_modular" in call_order
        assert "burn_subtitles_on_raw" in call_order

        # CRITICAL: Modular extraction MUST come BEFORE subtitles are burned on the final video
        idx_raw = call_order.index("vertical_render_raw")
        idx_mod = call_order.index("extract_modular")
        idx_burn = call_order.index("burn_subtitles_on_raw")
        assert idx_raw < idx_mod < idx_burn

        # Modular video must be extracted from the clean raw vertical file, NOT from final burned output!
        assert len(extracted_from_file) == 1
        assert "_raw" in extracted_from_file[0]


def test_separate_subtitles_flag_disables_burn_in():
    from main import build_parser, apply_cli_overrides
    parser = build_parser()
    args = parser.parse_args(["--separate-subtitles"])
    cfg = AppConfig()
    new_cfg = apply_cli_overrides(cfg, args)
    assert new_cfg.captions_export_overlay_video is True
    assert new_cfg.captions_burn_in_video is False

