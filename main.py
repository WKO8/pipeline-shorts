from __future__ import annotations

import argparse
import asyncio
import difflib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import List, Optional

import pysubs2

_SITECUSTOMIZE_PATH = Path(__file__).resolve().with_name("sitecustomize.py")
if _SITECUSTOMIZE_PATH.is_file():
    _sitecustomize_spec = importlib.util.spec_from_file_location(
        "_pipeline_sitecustomize", _SITECUSTOMIZE_PATH
    )
    _sitecustomize_module = importlib.util.module_from_spec(_sitecustomize_spec)
    sys.modules[_sitecustomize_spec.name] = _sitecustomize_module
    _sitecustomize_spec.loader.exec_module(_sitecustomize_module)

from src.quality_review import refine_render_clips, write_quality_review
from src.config import AppConfig, load_config
from src.curator import OllamaCurator, get_curator
from src.downloader import YouTubeDownloader
from src.models import ClipCandidate, ContextualOverlay, PipelineArtifacts, Transcript, SourceSpan
from src.renderer import ShortsRenderer
from src.rerenderer import (
    run_overlay_text_fix as _run_overlay_text_fix,
    run_rerender as _run_rerender,
)
from src.transcriber import WhisperTranscriber, release_gpu_memory, transcribe_audio
from src.upscaler import RealEsrganNcnnUpscaler
from src.youtube_discovery import YouTubeDiscoveryScraper
from src.native_subtitles_extractor import (
    extract_native_subtitles,
    native_segments_to_transcript,
    slice_segments_for_cut,
)
from src.jev_curator import JevTwoLayerCurator
from src.range_downloader import SelectiveRangeDownloader
from src.console_ui import (
    console,
    print_header,
    print_stage,
    print_success,
    print_warning,
    print_error,
    print_thread,
    print_cuts_table,
    print_discovered_videos_table,
    print_timing_table,
)
from src.utils import (
    new_run_dir,
    preserve_existing,
    file_stem_sanitized,
    hhmmss_to_seconds,
    parallel_convert_davinci,
    read_json,
    require_binaries,
    run_cmd,
    setup_logging,
    which,
    write_json,
)

LOGGER = logging.getLogger("pipeline.main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline-shorts-build",
        description=(
            "Full local pipeline for generating Shorts/TikTok clips from a YouTube URL:\n"
            "1) download video/audio, 2) transcribe with word timestamps,\n"
            "3) curate viral clips with Ollama, 4) render vertical clips with captions."
        ),
    )

    parser.add_argument(
        "--url",
        type=str,
        required=False,
        default="",
        help="YouTube video URL (single video).",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default="",
        help="Optional custom slug for output file names (defaults to derived from title).",
    )
    parser.add_argument(
        "--specific-moment",
        type=str,
        default="",
        help=(
            "Specific source-video interval(s) to process, in seconds or HH:MM:SS "
            "(e.g. '30-90', '00:00:30-00:01:30', or '30-60,120-150')."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=".env",
        help="Path to .env configuration file (default: .env).",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Override Whisper language (e.g., pt, en).",
    )
    parser.add_argument(
        "--audio-format",
        type=str,
        default="wav",
        choices=["wav", "mp3"],
        help="Extracted audio format for transcription (default: wav).",
    )
    parser.add_argument(
        "--max-clips",
        type=int,
        default=None,
        help="Override number of clips requested from curator.",
    )
    parser.add_argument(
        "--clip-min-seconds",
        type=int,
        default=None,
        help="Override minimum clip duration in seconds.",
    )
    parser.add_argument(
        "--clip-max-seconds",
        type=int,
        default=None,
        help="Override maximum clip duration in seconds.",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download stage (requires --video-path and --audio-path).",
    )
    parser.add_argument("--no-cache", action="store_true", help="Recompute stages while preserving existing checkpoints.")
    parser.add_argument("--align-full", action="store_true", help="Align the entire transcript before curation.")
    parser.add_argument("--no-editorial-review", action="store_true", help="Skip the local second editorial evaluation.")
    parser.add_argument(
        "--force-curate",
        action="store_true",
        help="Force re-curating clips even if .clips.json already exists.",
    )
    parser.add_argument(
        "--download-original-video",
        action="store_true",
        help=(
            "Download only the original YouTube video and stop after the download stage, "
            "without transcription, curation, or rendering."
        ),
    )
    parser.add_argument(
        "--video-path",
        type=str,
        default="",
        help="Local video path when --skip-download is used.",
    )
    parser.add_argument(
        "--audio-path",
        type=str,
        default="",
        help="Local audio path when --skip-download is used.",
    )
    parser.add_argument(
        "--transcript-json",
        type=str,
        default="",
        help="Optional existing transcript JSON to skip transcription stage.",
    )
    parser.add_argument(
        "--curator",
        type=str,
        default=None,
        choices=["auto", "deepseek", "gemini", "ollama", "jev"],
        help="Curator provider to use (default from .env or 'auto': DeepSeek with Gemini fallback).",
    )
    parser.add_argument(
        "--reasoning-effort",
        type=int,
        default=None,
        choices=[50, 75, 100],
        help="DeepSeek reasoning effort tier: 50 (low), 75 (high), 100 (max).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run until clip selection, without rendering final videos.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Skip confirmation prompts and render all curated clips automatically.",
    )
    parser.add_argument(
        "--captions-max-words",
        type=int,
        default=None,
        help="Override CAPTIONS_SHORT_PHRASE_MAX_WORDS (max words per caption moment).",
    )
    parser.add_argument(
        "--captions-max-chars",
        type=int,
        default=None,
        help="Override CAPTIONS_MAX_CHARS (max characters per caption moment).",
    )
    parser.add_argument(
        "--input",
        type=str,
        default="",
        help="Caminho para um arquivo de vídeo ou áudio local para processamento.",
    )
    parser.add_argument(
        "--subtitles-only",
        action="store_true",
        help="Gera apenas a transcrição (.json) e as legendas (.ass) para o arquivo inteiro, pulando a curadoria e renderização de clipes.",
    )
    parser.add_argument(
        "--cinematic-zoom",
        action="store_true",
        help="Apply post-processing dynamic zoom to the final clips.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    parser.add_argument(
        "--profile",
        type=int,
        choices=[3],
        default=3,
        help="Pipeline profile version (Profile 3 is the only supported version).",
    )
    parser.add_argument(
        "--rerender",
        action="store_true",
        help="Re-render an existing clip using edited subtitles and stored metadata.",
    )
    parser.add_argument(
        "--subtitles",
        type=str,
        default="",
        help="ASS subtitle file name/path to use in rerender mode.",
    )
    parser.add_argument(
        "--overlay-text-fix",
        action="store_true",
        help=(
            "Update only subtitle words and regenerate only the transparent overlay MOV, "
            "keeping original subtitle timing unchanged."
        ),
    )
    parser.add_argument(
        "--overlay-text-source",
        type=str,
        default="",
        help=(
            "Text source used by --overlay-text-fix. Accepts .txt (one line per subtitle "
            "event) or .ass (text copied from dialogue events)."
        ),
    )
    parser.add_argument(
        "--overlay-output",
        type=str,
        default="",
        help=(
            "Optional output path for overlay MOV in --overlay-text-fix mode. "
            "Defaults to metadata overlay path."
        ),
    )
    parser.add_argument(
        "--keep-silence",
        action="store_true",
        help="Disable automatic silence removal (keep pauses).",
    )
    parser.add_argument("--narrative-edit", action="store_true", help="Local AI edits complete stories and removes digressions.")
    parser.add_argument("--semantic-montage", action="store_true", help="Find distant related passages with CPU multilingual embeddings.")
    parser.add_argument("--jev-triage", action="store_true", help="Enable optional budgeted TypeSafe Jev triage for semantic montage; uncertain/unavailable decisions use the local editor.")
    parser.add_argument("--download-embedding-model", action="store_true", help="Allow public embedding model download into the project (no paid API).")
    parser.add_argument("--narrative-montage", action="store_true", help="Map the full podcast and optionally join distant passages of the same story (local AI).")
    parser.add_argument("--hook-preview", action="store_true", help="Optionally repeat an exact striking excerpt before the complete narrative.")
    parser.add_argument("--hook-max-seconds", type=float, default=None, help="Maximum repeated hook length (default 12s).")
    parser.add_argument("--silence-threshold-db", type=float, default=None, help="Silence level in dB (default -40).")
    parser.add_argument("--silence-min-seconds", type=float, default=None, help="Only shorten pauses at least this long (default 0.8s).")
    parser.add_argument("--silence-padding-seconds", type=float, default=None, help="Preserve this margin on EACH side of a pause (default 0.15s).")
    parser.add_argument(
        "--remove-silence", action="store_true", help="Enable silence removal"), parser.add_argument("--clipe",
        "--clip",
        dest="clipe",
        type=str,
        default="",
        help="Target clip file name/path to re-render in rerender mode.",
    )
    parser.add_argument(
        "--duo-disable-windows",
        type=str,
        default="",
        help="Comma-separated duo windows to disable in rerender mode (e.g., '13-14,17-19').",
    )
    parser.add_argument(
        "--duo-disable-pad",
        type=float,
        default=0.08,
        help="Extra seconds added before/after each disabled duo window in rerender mode.",
    )
    parser.add_argument(
        "--retrack",
        action="store_true",
        help="Force tracking recalculation in rerender mode instead of reusing saved tracking sidecar.",
    )
    parser.add_argument(
        "--rerender-allow-jump-cuts",
        action="store_true",
        help=(
            "Allow silence jump-cuts during rerender. Disabled by default to preserve "
            "original timing and avoid A/V desync when only adjusting subtitles/layout."
        ),
    )
    parser.add_argument(
        "--focus-fix-windows",
        type=str,
        default="",
        help=(
            "Comma-separated windows (clip-relative seconds) to stabilize focus/crop in rerender "
            "(e.g., '34-44')."
        ),
    )
    parser.add_argument(
        "--focus-fix-pad",
        type=float,
        default=0.20,
        help="Extra seconds added before/after each focus-fix window.",
    )
    parser.add_argument(
        "--rerender-full-frame",
        action="store_true",
        help=(
            "Disable single-face crop and render full-frame (no zoom) in rerender mode. "
            "Useful when tracking loses the speaker and focuses on empty areas."
        ),
    )
    parser.add_argument(
        "--single-disable-windows",
        type=str,
        default="",
        help=(
            "Comma-separated windows (clip-relative seconds) to disable single mode only "
            "in those intervals, switching to fit-to-width full-frame (e.g., '34-44')."
        ),
    )
    parser.add_argument(
        "--single-disable-pad",
        type=float,
        default=0.20,
        help="Extra seconds added before/after each single-disable window.",
    )
    parser.add_argument(
        "--upscale",
        action="store_true",
        help="Enable AI upscaling stage after rendering clips.",
    )
    parser.add_argument(
        "--upscale-scale",
        type=int,
        choices=[2, 3, 4],
        default=None,
        help="Override UPSCALING_SCALE for this run.",
    )
    parser.add_argument(
        "--upscale-face-model",
        type=str,
        choices=["gfpgan", "codeformer"],
        default=None,
        help="Enable face enhancement and choose face restorer backend.",
    )
    parser.add_argument(
        "--upscale-codeformer-fidelity",
        type=float,
        default=None,
        help="Fidelity weight for CodeFormer (0 to 1). 0 is better quality, 1 is better identity.",
    )
    parser.add_argument(
        "--upscale-face-only-codeformer",
        action="store_true",
        default=False,
        dest="upscale_face_only_codeformer",
        help=(
            "Apply CodeFormer face restoration only (no ESRGAN), keeping original resolution. "
            "Much faster than ESRGAN+CodeFormer (~15-20min for 60s vs ~2h). "
            "Use with --clipe to apply to a specific clip directly."
        ),
    )
    parser.add_argument(
        "--separate-subtitles",
        action="store_true",
        default=False,
        dest="separate_subtitles",
        help=(
            "Export subtitles as a separate transparent overlay file (.overlay.mov) "
            "instead of burning them into the video. "
            "Equivalent to CAPTIONS_BURN_IN_VIDEO=false + CAPTIONS_EXPORT_OVERLAY_VIDEO=true."
        ),
    )
    parser.add_argument(
        "--enrich-clip",
        action="store_true",
        help=(
            "Run AI Curator on specific-moment cuts to add B-rolls and Sound Effects."
        ),
    )
    parser.add_argument(
        "--davinci",
        action="store_true",
        help="Enable DaVinci Resolve compatibility preset (DNxHR + PCM).",
    )
    parser.add_argument(
        "--output-ext",
        type=str,
        choices=["mp4", "mov"],
        default=None,
        help="Output video file extension (mp4 or mov).",
    )
    parser.add_argument(
        "--convert-davinci",
        action="store_true",
        help="Parallel DaVinci Resolve conversion of a source video.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="",
        help="Output video path for standalone conversion.",
    )
    parser.add_argument(
        "--discover",
        type=str,
        nargs="?",
        const="",
        default=None,
        help="Buscar vídeos longos no YouTube e ranquear com Jev (TypeSafe AI) por potencial viral.",
    )
    parser.add_argument(
        "--discover-limit",
        type=int,
        default=15,
        help="Limite de candidatos na busca do YouTube (padrão: 15).",
    )
    parser.add_argument(
        "--discover-min-score",
        type=float,
        default=0.60,
        help="Nota mínima do Jev para reter candidatos na descoberta (padrão: 0.60).",
    )
    parser.add_argument(
        "--auto-pipeline",
        action="store_true",
        help="Após a descoberta no YouTube, seleciona automaticamente o vídeo #1 e executa o pipeline.",
    )
    parser.add_argument(
        "--jev-curate",
        action="store_true",
        help="Ativa a curadoria cognitiva em duas camadas com o Jev (Camada 1: ganchos 60s, Camada 2: narrativas 3-5m).",
    )
    parser.add_argument(
        "--http-range",
        action="store_true",
        help="Baixa apenas os trechos de cada corte via HTTP Range no yt-dlp, evitando baixar o vídeo inteiro.",
    )
    parser.add_argument(
        "--download-workers",
        type=int,
        default=3,
        help="Número de threads simultâneas para download de cortes via HTTP Range (padrão: 3).",
    )
    return parser


def validate_runtime_dependencies(include_downloader: bool = True) -> None:
    # External binaries required by this pipeline.
    binaries = ["ffmpeg"]
    if include_downloader:
        binaries.append("yt-dlp")
    require_binaries(binaries)


def make_slug(url: str, title: str = "", user_slug: str = "") -> str:
    if user_slug.strip():
        return file_stem_sanitized(user_slug.strip(), max_len=80)

    if title.strip():
        return file_stem_sanitized(title.strip(), max_len=80)

    # fallback from URL
    cleaned = re.sub(r"https?://", "", url.strip(), flags=re.IGNORECASE)
    cleaned = cleaned.replace("/", "_").replace("?", "_").replace("&", "_")
    return file_stem_sanitized(cleaned, max_len=80)


def _profile_clips_dir(cfg: AppConfig, slug: str) -> Path:
    return cfg.clips_dir / f"profile_{int(cfg.pipeline_profile)}" / slug


def apply_cli_overrides(cfg: AppConfig, args: argparse.Namespace) -> AppConfig:
    if getattr(args, "no_cache", False):
        cfg.stage_cache_enabled = False
    if getattr(args, "force_curate", False):
        cfg.curation_cache_enabled = False
    if getattr(args, "align_full", False):
        cfg.selective_alignment = False
    if getattr(args, "no_editorial_review", False):
        cfg.editorial_review_enabled = False
    selected_profile = (
        int(args.profile) if args.profile is not None else int(cfg.pipeline_profile)
    )
    _apply_profile_overrides(cfg=cfg, profile=selected_profile)
    if args.max_clips is not None:
        cfg.ollama_clips_count = max(1, int(args.max_clips))
    if args.curator is not None:
        cfg.curator_provider = args.curator
        if args.curator == "jev":
            cfg.jev_enabled = True
            setattr(args, "jev_curate", True)
    if getattr(args, "reasoning_effort", None) is not None:
        cfg.deepseek_reasoning_effort = int(args.reasoning_effort)
    if args.clip_min_seconds is not None:
        cfg.clip_min_seconds = max(1, int(args.clip_min_seconds))
    if args.clip_max_seconds is not None:
        cfg.clip_max_seconds = max(1, int(args.clip_max_seconds))
    if args.upscale:
        cfg.upscaling_enabled = True
        cfg.upscaling_model = "realesr-general-x4v3"
        cfg.upscaling_scale = 2
        cfg.captions_burn_in_video = False
        cfg.captions_export_overlay_video = True
        # Only default face enhancement when --upscale-face-model is not explicitly
        # provided and the user hasn't disabled it in .env (UPSCALING_FACE_ENHANCE=false).
        if args.upscale_face_model is None and cfg.upscaling_face_enhance:
            cfg.upscaling_face_model = cfg.upscaling_face_model or "gfpgan"
    if args.upscale_scale is not None:
        cfg.upscaling_scale = int(args.upscale_scale)
    if args.upscale_face_model is not None:
        cfg.upscaling_enabled = True
        cfg.upscaling_face_enhance = True
        cfg.upscaling_face_model = args.upscale_face_model.strip().lower()
    if args.upscale_codeformer_fidelity is not None:
        cfg.upscaling_codeformer_fidelity = float(args.upscale_codeformer_fidelity)
    if getattr(args, "separate_subtitles", False):
        cfg.captions_export_overlay_video = True
        cfg.captions_burn_in_video = False
    if getattr(args, "upscale_face_only_codeformer", False):
        cfg.upscaling_face_only_codeformer = True
        cfg.upscaling_enabled = True
    if getattr(args, "cinematic_zoom", False):
        cfg.cinematic_zoom = True
    if getattr(args, "enrich_clip", False):
        cfg.enrich_clip = True
    if getattr(args, "captions_max_chars", None) is not None:
        cfg.captions_max_chars = int(args.captions_max_chars)
    if getattr(args, "captions_max_words", None) is not None:
        cfg.captions_short_phrase_max_words = int(args.captions_max_words)
    if getattr(args, "output_ext", None) is not None:
        cfg.output_video_extension = args.output_ext.strip().lower()
    if getattr(args, "davinci", False):
        cfg.davinci_resolve_compatibility = True
        cfg.output_video_extension = "mov"
    if getattr(args, "keep_silence", False):
        cfg.silence_jump_cuts_enabled = False
    elif getattr(args, "remove_silence", False):
        cfg.silence_jump_cuts_enabled = True
    for arg, field in (("narrative_edit", "narrative_edit_enabled"),
                       ("semantic_montage", "semantic_montage_enabled"),
                       ("jev_triage", "jev_enabled"),
                       ("jev_curate", "jev_enabled"),
                       ("download_embedding_model", "semantic_allow_download"),
                       ("narrative_montage", "narrative_montage_enabled"),
                       ("hook_preview", "hook_preview_enabled")):
        if getattr(args, arg, False):
            setattr(cfg, field, True)
    for arg, field in (("hook_max_seconds", "hook_preview_max_seconds"),
                       ("silence_threshold_db", "silence_noise_db"),
                       ("silence_min_seconds", "silence_min_duration_seconds"),
                       ("silence_padding_seconds", "silence_padding_seconds")):
        if getattr(args, arg, None) is not None:
            setattr(cfg, field, getattr(args, arg))
    cfg.validate()
    return cfg


def _run_face_only_codeformer_standalone(
    cfg: AppConfig,
    args: argparse.Namespace,
) -> int:
    """Standalone mode: apply CodeFormer face restoration to a specific clip.

    Usage:
        python main.py --upscale-face-only-codeformer --clipe path/to/clip.mp4

    Restores faces on every frame of the clip, keeping original resolution.
    Overwrites the clip in-place.
    """
    clip_path_str = (args.clipe or "").strip()
    if not clip_path_str:
        LOGGER.error(
            "--upscale-face-only-codeformer requires --clipe <path/to/clip.mp4>"
        )
        return 2

    clip_path = Path(clip_path_str).expanduser().resolve()
    if not clip_path.exists():
        LOGGER.error("Clip not found: %s", clip_path)
        return 2

    LOGGER.info(
        "Face-only CodeFormer | clip=%s | fidelity=%.2f",
        clip_path.name,
        cfg.upscaling_codeformer_fidelity,
    )

    upscaler = RealEsrganNcnnUpscaler(cfg)
    result = upscaler.upscale_video_inplace(clip_path)

    summary = {
        "status": "ok",
        "mode": "face_only_codeformer",
        "clip": str(result.output_path),
        "fidelity": cfg.upscaling_codeformer_fidelity,
        "backend": result.backend,
        "face_model": result.face_model,
    }
    _apply_post_processing_zoom(cfg, [result])
    
    # Update summary with new path if cinematic zoom was applied (it moves file)
    summary["clip_path"] = str(result.output_path)
    
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _run_optional_upscaling(
    cfg: AppConfig,
    render_results: List,
) -> list[dict]:
    if not cfg.upscaling_enabled:
        return []

    LOGGER.info(
        "AI upscaling enabled | face_enhance=%s | face_model=%s",
        cfg.upscaling_face_enhance,
        cfg.upscaling_face_model,
    )
    upscaler = RealEsrganNcnnUpscaler(cfg)
    upscaled: list[dict] = []
    for item in render_results:
        result = upscaler.upscale_video_inplace(item.output_path)
        upscaled.append(
            {
                "output_path": str(result.output_path),
                "scale": result.scale,
                "model": result.model,
                "backend": result.backend,
                "face_enhance": result.face_enhance,
                "face_model": result.face_model,
                "tile_size": result.tile_size,
                "threads": result.threads,
                "gpu_id": result.gpu_id,
            }
        )
    LOGGER.info("Upscaling complete for %d clip(s).", len(upscaled))
    return upscaled


def _apply_profile_overrides(cfg: AppConfig, profile: int) -> None:
    # Keep .env/UI preferences. The former preset silently forced giant,
    # one-word yellow captions and expensive alpha-video exports on every run.
    cfg.pipeline_profile = profile


def load_transcript_from_json(path: Path) -> Transcript:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for seg in payload.get("segments", []):
        words = []
        for w in seg.get("words", []):
            words.append(
                {
                    "text": str(w.get("text", "")).strip(),
                    "start": float(w.get("start", 0.0) or 0.0),
                    "end": float(w.get("end", 0.0) or 0.0),
                    "probability": w.get("probability", None),
                }
            )
        segments.append(
            {
                "id": int(seg.get("id", len(segments))),
                "start": float(seg.get("start", 0.0) or 0.0),
                "end": float(seg.get("end", 0.0) or 0.0),
                "text": str(seg.get("text", "")).strip(),
                "words": words,
            }
        )

    from src.models import TranscriptSegment, WordTimestamp  # lazy import for clarity

    transcript = Transcript(
        language=str(payload.get("language", "unknown")),
        source_audio=Path(payload["source_audio"]).resolve()
        if payload.get("source_audio")
        else None,
        source_video=Path(payload["source_video"]).resolve()
        if payload.get("source_video")
        else None,
        segments=[],
        timing_source=payload.get("timing_source", "unknown"),
    )

    for seg in segments:
        transcript.segments.append(
            TranscriptSegment(
                id=seg["id"],
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
                words=[
                    WordTimestamp(
                        text=w["text"],
                        start=w["start"],
                        end=w["end"],
                        probability=w["probability"],
                    )
                    for w in seg["words"]
                ],
            )
        )

    return transcript


def save_clip_candidates(path: Path, clips: List[ClipCandidate]) -> Path:
    data = {"clips": [c.to_dict() for c in clips]}
    return write_json(path, data)


def load_clip_candidates(path: Path) -> List[ClipCandidate]:
    data = read_json(path)
    raw_clips = data.get("clips", []) if isinstance(data, dict) else data
    clips = []
    for item in raw_clips:
        overlays = []
        for ov in item.get("contextual_overlays", []):
            overlays.append(
                ContextualOverlay(
                    asset_path=ov.get("asset_path", ""),
                    start=float(ov.get("start", 0.0)),
                    end=float(ov.get("end", 0.0)),
                    y_offset=int(ov.get("y_offset", 400)),
                    max_width=int(ov.get("max_width", 420)),
                    scale=float(ov.get("scale", 1.0)),
                )
            )
        clips.append(
            ClipCandidate(
                title=str(item.get("title", "")),
                start=float(item.get("start", 0.0)),
                end=float(item.get("end", 0.0)),
                reason=str(item.get("reason", "")),
                score=float(item.get("score", 0.0)),
                tags=list(item.get("tags", [])),
                layout_mode=str(item.get("layout_mode", "single")),
                b_roll_overlay=item.get("b_roll_overlay"),
                contextual_overlays=overlays,
                source_spans=[SourceSpan(**part) for part in item.get("source_spans", [])],
            )
        )
    return clips


def _build_specific_moment_manual_clip(
    duration_s: float,
    slug: str,
    index: int,
    source_window: tuple[float, float],
) -> ClipCandidate:
    if duration_s <= 0:
        raise RuntimeError("Specific-moment clip has zero duration after trim.")

    source_start, source_end = source_window

    return ClipCandidate(
        title=f"{slug}_specific_moment_{index:02d}",
        start=0.0,
        end=duration_s,
        reason=(
            "Manual specific-moment clip (curation bypassed). "
            f"Source interval: {source_start:.3f}-{source_end:.3f}s."
        ),
        score=10.0,
        tags=["specific_moment", "manual_cut", f"segment_{index:02d}"],
        layout_mode="single",
    )


def _clip_from_payload(payload: dict) -> ClipCandidate:
    overlays: list[ContextualOverlay] = []
    for raw in payload.get("contextual_overlays", []):
        if not isinstance(raw, dict):
            continue
        overlays.append(
            ContextualOverlay(
                asset_path=str(raw.get("asset_path", raw.get("path", ""))).strip(),
                start=float(raw.get("start", 0.0) or 0.0),
                end=float(raw.get("end", 0.0) or 0.0),
                y_offset=int(raw.get("y_offset", 400)),
                scale=float(raw.get("scale", 1.0)),
                max_width=int(raw.get("max_width", 420)),
            )
        )
    return ClipCandidate(
        title=str(payload.get("title", "clip")).strip() or "clip",
        start=float(payload.get("start", 0.0) or 0.0),
        end=float(payload.get("end", 0.0) or 0.0),
        reason=str(payload.get("reason", "")).strip(),
        score=float(payload.get("score", 0.0) or 0.0),
        tags=[str(tag) for tag in payload.get("tags", []) if str(tag).strip()],
        layout_mode=str(payload.get("layout_mode", "single")),
        b_roll_overlay=payload.get("b_roll_overlay"),
        contextual_overlays=overlays,
        source_spans=[SourceSpan(**part) for part in payload.get("source_spans", [])],
    )


def _resolve_existing_file(path_or_name: str, search_roots: list[Path]) -> Path:
    raw = (path_or_name or "").strip()
    if not raw:
        raise ValueError("Empty file argument.")
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
        raise FileNotFoundError(f"File not found: {candidate}")
    local_candidate = candidate.resolve()
    if local_candidate.exists() and local_candidate.is_file():
        return local_candidate

    filename = candidate.name
    for root in search_roots:
        if not root.exists():
            continue
        prioritized = (root / filename).resolve()
        if prioritized.exists() and prioritized.is_file():
            return prioritized
        scoped = sorted(
            {path.resolve() for path in root.rglob(filename) if path.is_file()}
        )
        if len(scoped) == 1:
            return scoped[0]
        if len(scoped) > 1:
            options = "\n".join(str(path) for path in scoped[:8])
            raise RuntimeError(
                f"Multiple files found for '{filename}' under {root}. Use full path.\n{options}"
            )
    matches: list[Path] = []
    for root in search_roots:
        if root.exists():
            matches.extend(path for path in root.rglob(filename) if path.is_file())
    unique = sorted({p.resolve() for p in matches})
    if not unique:
        raise FileNotFoundError(
            f"Could not find file '{filename}' in: {', '.join(str(p) for p in search_roots)}"
        )
    if len(unique) > 1:
        options = "\n".join(str(path) for path in unique[:8])
        raise RuntimeError(
            f"Multiple files found for '{filename}'. Use full path.\n{options}"
        )
    return unique[0]


def _parse_time_token(raw: str) -> float:
    token = (raw or "").strip()
    if not token:
        raise ValueError("Empty time token.")
    if ":" in token:
        return float(hhmmss_to_seconds(token))
    return float(token)


def _parse_window_ranges(raw: str) -> list[tuple[float, float]]:
    value = (raw or "").strip()
    if not value:
        return []
    out: list[tuple[float, float]] = []
    for chunk in value.split(","):
        piece = chunk.strip()
        if not piece:
            continue
        if "-" not in piece:
            raise ValueError(
                f"Invalid window '{piece}'. Use format start-end, e.g. 13-14 or 00:13-00:14."
            )
        left, right = piece.split("-", 1)
        start = _parse_time_token(left)
        end = _parse_time_token(right)
        if end <= start:
            raise ValueError(f"Invalid window '{piece}': end must be > start.")
        out.append((start, end))
    out.sort(key=lambda x: x[0])
    return out


def _parse_specific_moments(raw: str) -> list[tuple[float, float]]:
    value = (raw or "").strip()
    if not value:
        return []

    moments = _parse_window_ranges(value)
    for start, end in moments:
        if start < 0 or end < 0:
            raise ValueError("--specific-moment values must be >= 0.")
    return moments


def _ffprobe_stream_duration_seconds(path: Path, stream_spec: str) -> float:
    """Return stream duration in seconds (0 if unknown / missing metadata)."""
    result = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            stream_spec,
            "-show_entries",
            "stream=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
    )
    raw = (result.stdout or "").strip()
    if not raw or raw.lower() in {"n/a", "nan"}:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _trimmed_av_matches_expected(
    path: Path, expected_duration: float, tol: float = 0.75
) -> bool:
    """True when video/audio streams are ~expected length and match each other."""
    v = _ffprobe_stream_duration_seconds(path, "v:0")
    a = _ffprobe_stream_duration_seconds(path, "a:0")
    if v <= 0.25 or a <= 0.25:
        return False
    if abs(v - a) > tol:
        return False
    slack = max(2.5, 0.035 * max(expected_duration, 1.0))
    if abs(v - expected_duration) > slack or abs(a - expected_duration) > slack:
        return False
    return True


def _trim_media_to_specific_moment(
    cfg: AppConfig,
    video_path: Path,
    slug: str,
    moment: tuple[float, float],
    audio_format: str,
    interval_index: int,
) -> tuple[Path, Path]:
    start_s, end_s = moment
    duration_s = end_s - start_s
    if duration_s <= 0:
        raise ValueError("Specific interval duration must be greater than zero.")

    from uuid import uuid4
    suffix = f"specific_moment_{int(interval_index):02d}_{uuid4().hex[:8]}"
    trimmed_video = cfg.temp_dir / f"{slug}.{suffix}.mp4"
    trimmed_audio = cfg.audio_dir / f"{slug}.{suffix}.{audio_format}"

    def _run_intermediate_trim(*, fast_preroll: bool) -> None:
        """
        Re-encode the window for downstream OpenCV / NVENC render.

        **Do not use h264_nvenc for this intermediate file**: on long YouTube-derived
        inputs, NVENC + seek combinations frequently mux a **truncated video stream**
        (~10–20s) while audio keeps the requested ``-t`` — the exact bug reported.

        libx264 is only used for this temp slice; final shorts still use NVENC in
        ``ShortsRenderer``.
        """
        cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        cmd.extend(["-y", "-threads", "4", "-v", "warning"])
        if fast_preroll:
            seek_margin = 28.0
            first_ss = max(0.0, start_s - seek_margin)
            inpoint_ss = max(0.0, start_s - first_ss)
            cmd.extend(["-ss", f"{first_ss:.3f}", "-i", str(video_path)])
            if inpoint_ss > 1e-3:
                cmd.extend(["-ss", f"{inpoint_ss:.3f}"])
        else:
            cmd.extend(["-i", str(video_path), "-ss", f"{start_s:.3f}"])
        cmd.extend(
            [
                "-t",
                f"{duration_s:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(trimmed_video),
            ]
        )
        preserve_existing(trimmed_video)
        run_cmd(cmd, check=True)

    # Re-encode to keep accurate cut boundaries for downstream timing-sensitive stages.
    _run_intermediate_trim(fast_preroll=True)
    if not _trimmed_av_matches_expected(trimmed_video, duration_s):
        LOGGER.warning(
            "specific-moment trim: video/audio length mismatch after fast seek "
            "(expected≈%.2fs). Retrying with full decode seek (slower but reliable).",
            duration_s,
        )
        _run_intermediate_trim(fast_preroll=False)
    if not _trimmed_av_matches_expected(trimmed_video, duration_s):
        raise RuntimeError(
            f"Trimmed clip has inconsistent video/audio duration (expected≈{duration_s:.2f}s). "
            f"Inspect: {trimmed_video}"
        )

    if audio_format == "wav":
        audio_cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        audio_cmd.extend(
            [
                "-y",
                "-threads",
                "4",
                "-i",
                str(trimmed_video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(trimmed_audio),
            ]
        )
    elif audio_format == "mp3":
        audio_cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        audio_cmd.extend(
            [
                "-y",
                "-threads",
                "4",
                "-i",
                str(trimmed_video),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(trimmed_audio),
            ]
        )
    else:
        raise ValueError("audio_format must be 'wav' or 'mp3'.")

    run_cmd(audio_cmd, check=True)

    if not trimmed_video.exists():
        raise RuntimeError(f"Specific-moment trimmed video not found: {trimmed_video}")
    if not trimmed_audio.exists():
        raise RuntimeError(f"Specific-moment trimmed audio not found: {trimmed_audio}")

    return trimmed_video, trimmed_audio


# ---------------------------------------------------------------------------
# Note: Legacy rerender, overlay text fix, and tracking window stabilization
# routines have been refactored into src/rerenderer.py
# ---------------------------------------------------------------------------


def _apply_post_processing_zoom(cfg: AppConfig, render_results: List[RenderResult]) -> None:
    if not cfg.cinematic_zoom:
        return
    LOGGER.info("Applying post-processing cinematic zoom to rendered clips...")
    import shutil
    from src.post_processor import apply_cinematic_zoom
    for result in render_results:
        if getattr(result, "zoom_applied", False):
            LOGGER.info("Cinematic zoom already applied before subtitles: %s", getattr(result.output_path, "name", result.output_path))
            continue
        final_path = Path(result.output_path)
        layout = getattr(result.clip, "layout_mode", "single") if hasattr(result, "clip") and result.clip else "single"
        if layout in ("duo_split", "reaction_split"):
            LOGGER.info("Skipping cinematic zoom for split-screen layout (%s): %s", layout, final_path.name)
            continue
        zoomed_path = final_path.with_stem(final_path.stem + "_zoomed")
        
        # Pass tracking points to the post-processor for Face-Lock
        tracking_points = result.tracking_data.points if hasattr(result, "tracking_data") and result.tracking_data else None
        
        try:
            # Apply to main clip
            apply_cinematic_zoom(final_path, zoomed_path, tracking_points)
            preserve_existing(final_path)
            shutil.copy2(zoomed_path, final_path)
            
            # CRITICAL: If Profile 3 modular files exist, update them too!
            video_modular = final_path.parent / "modular" / f"{final_path.stem}_video.{cfg.output_video_extension}"
            if not video_modular.exists():
                video_modular = final_path.with_name(f"{final_path.stem}_video.{cfg.output_video_extension}")
            if video_modular.exists():
                LOGGER.info(f"Updating modular video with zoom: {video_modular.name}")
                mod_zoomed = video_modular.with_stem(video_modular.stem + "_zoomed")
                apply_cinematic_zoom(video_modular, mod_zoomed, tracking_points)
                preserve_existing(video_modular)
                shutil.copy2(mod_zoomed, video_modular)
                
        except Exception as e:
            LOGGER.error(f"Failed to apply cinematic zoom to {final_path}: {e}")
            if zoomed_path.exists():
                LOGGER.info("Preserved zoom output: %s", zoomed_path)

def _run_convert_davinci_standalone(
    cfg: AppConfig,
    args: argparse.Namespace,
) -> int:
    """Standalone mode: convert a video to DaVinci Resolve compatibility format in parallel."""
    input_path_str = (args.input or "").strip()
    output_path_str = (args.output or "").strip()

    if not input_path_str:
        LOGGER.error("--convert-davinci requires --input <path/to/video>")
        return 2

    input_path = Path(input_path_str).expanduser().resolve()
    if not input_path.exists():
        LOGGER.error("Input file not found: %s", input_path)
        return 2

    if not output_path_str:
        output_path = input_path.with_suffix(".mov")
        if output_path == input_path:
            output_path = input_path.parent / f"{input_path.stem}_davinci.mov"
    else:
        output_path = Path(output_path_str).expanduser().resolve()

    LOGGER.info("Starting Parallel DaVinci Conversion: %s -> %s", input_path.name, output_path.name)
    try:
        parallel_convert_davinci(input_path=input_path, output_path=output_path)
        LOGGER.info("Conversion finished successfully: %s", output_path)
        return 0
    except Exception as exc:
        LOGGER.error("Conversion failed: %s", exc)
        return 1


def _run_discover_standalone(
    cfg: AppConfig,
    args: argparse.Namespace,
) -> int:
    """Standalone mode: discover and rank long YouTube videos using Jev (TypeSafe AI)."""
    import asyncio
    api_key = cfg.typesafe_api_key or os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    scraper = YouTubeDiscoveryScraper(api_key=api_key, model=cfg.jev_model)
    query = (args.discover or "").strip()
    LOGGER.info("Iniciando Descoberta no YouTube via Jev (query=%s)...", query or "trending")

    print_stage("Descobrindo e Ranqueando Vídeos Longos no YouTube com o Jev")
    candidates = asyncio.run(
        scraper.discover_and_rank(
            query=query if query else None,
            limit=args.discover_limit,
            min_score=args.discover_min_score,
        )
    )
    if not candidates:
        print_warning("YouTube Discovery", f"Nenhum candidato atingiu o score mínimo de {args.discover_min_score:.2f}")
        return 0

    slug_part = file_stem_sanitized(query) if query else "trending"
    out_json = cfg.data_dir / f"curated_videos_{slug_part}.json"
    scraper.save_curated_videos_json(
        videos=candidates,
        output_path=out_json,
        search_query=query,
        min_score=args.discover_min_score,
    )

    print_discovered_videos_table(candidates)
    print_success("YouTube Discovery", f"{len(candidates)} vídeo(s) avaliados e salvos em [bold cyan]{out_json}[/bold cyan]")

    if getattr(args, "auto_pipeline", False):
        top_cand = candidates[0]
        console.print(f"  [bold green]🚀 [Auto-Pipeline][/bold green] Prosseguindo com o vídeo #1: [bold cyan]{top_cand['title']}[/bold cyan] ({top_cand['url']})")
        args.url = top_cand["url"]
        args.slug = file_stem_sanitized(top_cand["title"])
        return -1
    else:
        top_cand = candidates[0]
        console.print("\n[bold cyan]💡 Para processar cortes do vídeo melhor ranqueado, execute:[/bold cyan]")
        console.print(f"  [bold yellow]python main.py --url \"{top_cand['url']}\" --jev-curate --http-range[/bold yellow]\n")
        return 0


def _run_http_range_pipeline(
    cfg: AppConfig,
    args: argparse.Namespace,
    slug: str,
    url: str,
) -> int:
    """Full pipeline using HTTP Range selective downloads and two-layer Jev curation."""
    import asyncio
    import time
    t_start = time.time()
    timings: dict[str, float] = {}

    console.print(f"\n[bold magenta]⚡ Iniciando Pipeline Seletivo HTTP Range com Curadoria Jev[/bold magenta]")
    console.print(f"  [cyan]URL:[/cyan] {url}")
    console.print(f"  [cyan]Slug:[/cyan] [bold cyan]{slug}[/bold cyan]\n")

    # 1. Extração / Obtenção de Transcrição
    print_stage("1. Extração / Obtenção de Transcrição")
    t_sub_start = time.time()
    segments = None
    title = slug

    if args.transcript_json.strip():
        trans_file = Path(args.transcript_json).expanduser().resolve()
        if trans_file.exists():
            loaded_tr = load_transcript_from_json(trans_file)
            segments = [{"start": s.start, "end": s.end, "text": s.text} for s in loaded_tr.segments]
            print_success("Transcrição", f"Carregada transcrição existente com {len(segments)} segmentos: {trans_file.name}")

    if not segments:
        LOGGER.info("Tentando extração ultrarrápida de legendas nativas via VTT...")
        native_segs, meta = extract_native_subtitles(url)
        if native_segs:
            segments = native_segs
            title = meta.get("title") or slug
            timings["1. Extração / Transcrição"] = time.time() - t_sub_start
            print_success("Legendas Nativas", f"{len(segments)} segmentos extraídos via VTT", elapsed=timings["1. Extração / Transcrição"])
        else:
            print_warning("Legendas Nativas", "Não encontradas. Baixando apenas áudio leve para transcrição Whisper...")
            cfg.ensure_directories()
            temp_audio = cfg.audio_dir / f"{slug}_full_pre_curate.wav"
            if not temp_audio.exists():
                yt_cmd = which("yt-dlp") or "yt-dlp"
                run_cmd([
                    yt_cmd, "--no-warnings", "--no-playlist",
                    "-f", "ba[ext=m4a]/ba/bestaudio",
                    "-x", "--audio-format", "wav",
                    "-o", str(temp_audio),
                    url
                ], check=True)
            tr = transcribe_audio(cfg, audio_path=temp_audio, language=args.language)
            segments = [{"start": s.start, "end": s.end, "text": s.text} for s in tr.segments]
            timings["1. Extração / Transcrição"] = time.time() - t_sub_start
            print_success("Transcrição Whisper", f"{len(segments)} segmentos obtidos", elapsed=timings["1. Extração / Transcrição"])
    else:
        timings["1. Extração / Transcrição"] = time.time() - t_sub_start

    if not segments:
        print_error("Transcrição", "Não foi possível obter transcrição para curadoria.")
        return 1

    # 2. Curadoria Cognitiva em Duas Camadas com o Jev
    print_stage("2. Curadoria Cognitiva em Duas Camadas com o Jev")
    t_curate_start = time.time()
    api_key = cfg.typesafe_api_key or os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY")
    curator = JevTwoLayerCurator(api_key=api_key, model=cfg.jev_model)
    min_dur = float(args.clip_min_seconds) if getattr(args, "clip_min_seconds", None) is not None else 20.0
    max_dur = float(args.clip_max_seconds) if getattr(args, "clip_max_seconds", None) is not None else 180.0
    max_clips = getattr(args, "max_clips", None) or cfg.ollama_clips_count
    viral_cuts = asyncio.run(
        curator.curate(
            segments=segments,
            min_duration=min_dur,
            max_duration=max_dur,
            max_clips=max_clips,
        )
    )
    timings["2. Curadoria Jev (Ganchos + Narrativa)"] = time.time() - t_curate_start

    if not viral_cuts:
        print_warning("Jev Curator", "Nenhum corte viral aprovado pelos critérios cognitivos.")
        return 0

    cuts_json = cfg.data_dir / f"viral_cuts_{slug}.json"
    curator.save_viral_cuts_json(viral_cuts, cuts_json, video_id=slug, title=title)
    print_success("Jev Curator", f"{len(viral_cuts)} cortes virais aprovados e salvos em {cuts_json.name}", elapsed=timings["2. Curadoria Jev (Ganchos + Narrativa)"])

    print_cuts_table(viral_cuts, title="🔥 Cortes Virais Curados pelo Jev")

    if args.dry_run:
        console.print("  [bold yellow]⚠ [Dry-Run][/bold yellow] Modo de simulação ativado; encerrando antes do download e renderização.")
        return 0

    # 3. Pipeline Concorrente: Download Seletivo e Renderização 9:16 em Streaming
    print_stage("3. Pipeline Concorrente: Download Seletivo + Renderização 9:16 (Streaming)")
    t_stage3_start = time.time()
    range_downloader = SelectiveRangeDownloader(
        cookies_file=cfg.ytdlp_cookies_file,
        cookies_from_browser=cfg.ytdlp_cookies_from_browser,
    )
    download_workers = getattr(args, "download_workers", 3) or 3

    out_dir = new_run_dir(_profile_clips_dir(cfg=cfg, slug=slug))
    renderer = ShortsRenderer(cfg)
    render_results = []
    downloaded_cuts: list[dict[str, Any]] = []
    processed_count = 0
    total_cuts = len(viral_cuts)

    # Inicia downloads paralelos em background e processa cada corte assim que o download termina
    for cut in range_downloader.download_cuts_stream(
        url=url,
        cuts=viral_cuts,
        output_dir=cfg.downloads_dir,
        video_slug=slug,
        max_workers=download_workers,
    ):
        downloaded_cuts.append(cut)
        processed_count += 1
        video_file_str = cut.get("video_file")
        if not video_file_str:
            print_error("Pipeline", f"Corte #{cut.get('cut_id', processed_count)} não possui arquivo de vídeo baixado.")
            continue
        cut_video_path = Path(video_file_str).resolve()
        if not cut_video_path.exists():
            print_error("Pipeline", f"Arquivo de vídeo do corte não encontrado: {cut_video_path}")
            continue

        idx = cut.get("cut_id", processed_count)
        console.print(f"\n  [bold cyan]🎬 [Processando Corte {idx}/{total_cuts} em Streaming][/bold cyan] {cut_video_path.name} ({cut['duration_seconds']:.1f}s)")
        cut_transcript_path = out_dir / f"{slug}_corte_{idx:02d}.transcript.json"

        # Transcrição com WhisperX (large-v3-turbo + alinhamento fonético) para sincronização perfeita
        cut_audio_path = cfg.audio_dir / f"{cut_video_path.stem}.wav"
        try:
            if not cut_audio_path.exists():
                run_cmd([
                    "ffmpeg", "-y", "-i", str(cut_video_path),
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    str(cut_audio_path)
                ], check=True)

            cut_transcript = transcribe_audio(
                config=cfg,
                audio_path=cut_audio_path,
                output_json_path=cut_transcript_path,
                language=args.language,
                defer_alignment=False,
            )
            cut_transcript.source_video = cut_video_path
        except Exception as whisper_err:
            if segments:
                print_warning("Whisper", f"Falha na transcrição local ({whisper_err}); usando fallback nativo fatiado.")
                cut_transcript = slice_segments_for_cut(
                    segments=segments,
                    cut_start=float(cut["start_second"]),
                    cut_end=float(cut["end_second"]),
                    language=args.language or "pt",
                )
                cut_transcript.source_video = cut_video_path
                write_json(cut_transcript_path, cut_transcript.to_dict())
            else:
                raise

        actual_dur = cut_transcript.duration if cut_transcript.duration > 0 else cut["duration_seconds"]

        clip_obj = ClipCandidate(
            title=f"{slug} - Corte {idx}",
            start=0.0,
            end=actual_dur,
            score=float(cut["combined_score"]),
            reason=f"Gancho Jev: {cut['hook_score']:.2f} | Narrativa: {cut['narrative_score']:.2f}",
            tags=["jev", "viral-cut"],
        )

        res = renderer.render_single_clip(
            source_video=cut_video_path,
            transcript=cut_transcript,
            clip=clip_obj,
            output_dir=out_dir,
            slug=slug,
            index=idx,
            transcript_json_path=cut_transcript_path,
        )
        # Atualiza o clip com os timestamps reais da fonte para tabelas e manifest
        res.clip.start = float(cut["start_second"])
        res.clip.end = float(cut["end_second"])
        render_results.append(res)
        print_success("Renderer 9:16", f"Corte #{idx} finalizado: {Path(res.output_path).name}")

    if not downloaded_cuts:
        print_error("Pipeline", "Nenhum corte foi baixado com sucesso via HTTP Range.")
        return 1

    _apply_post_processing_zoom(cfg, render_results)
    upscaled = _run_optional_upscaling(cfg=cfg, render_results=render_results)
    timings["3. Pipeline Streaming (Download + Render)"] = time.time() - t_stage3_start

    # Benchmark e Tabela Final
    t_total = time.time() - t_start
    print_timing_table(timings, total_time=t_total)
    print_cuts_table(render_results, title="🎬 Shorts Renderizados Prontos para Publicação")

    manifest_path = out_dir / f"{slug}.manifest.json"
    manifest_data = {
        "status": "ok",
        "mode": "jev_http_range",
        "slug": slug,
        "cuts_count": len(downloaded_cuts),
        "rendered_count": len(render_results),
        "rendered": [r.to_dict() for r in render_results],
        "upscaled": upscaled,
        "timings": timings,
        "total_time_seconds": round(t_total, 2),
    }
    write_json(manifest_path, manifest_data)
    print_success("Pipeline", f"Manifesto salvo em [bold cyan]{manifest_path}[/bold cyan]")
    return 0


def main() -> int:
    artifacts = None
    parser = build_parser()
    args = parser.parse_args()

    level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=level)
    print_header()

    t_pipeline_start = time.time()
    timings: dict[str, float] = {}

    try:
        cfg = load_config(
            env_file=args.env_file, project_root=Path(__file__).resolve().parent
        )
        cfg = apply_cli_overrides(cfg, args)
        cfg.ensure_directories()
        validate_runtime_dependencies(
            include_downloader=not (args.rerender or args.overlay_text_fix)
        )
    except Exception as exc:
        LOGGER.error("Configuration/dependency error: %s", exc)
        print_error("Config", f"Erro de configuração ou dependência: {exc}")
        return 2

    artifacts = PipelineArtifacts(working_dir=cfg.project_root)

    # ---- Pre-Stage: Handle local --input
    if args.input.strip():
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            LOGGER.error("Arquivo de entrada não encontrado: %s", input_path)
            print_error("Input", f"Arquivo de entrada não encontrado: {input_path}")
            return 2
        
        args.skip_download = True
        ext = input_path.suffix.lower()
        if ext in {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv"}:
            args.video_path = str(input_path)
        else:
            args.audio_path = str(input_path)
            if not args.video_path.strip():
                args.video_path = ""

    try:
        specific_moments = _parse_specific_moments(args.specific_moment)
        if args.overlay_text_fix:
            if specific_moments:
                raise ValueError(
                    "--specific-moment is not supported with --overlay-text-fix."
                )
            return _run_overlay_text_fix(cfg=cfg, args=args)
        if getattr(args, "convert_davinci", False):
            return _run_convert_davinci_standalone(cfg=cfg, args=args)
        if getattr(args, "upscale_face_only_codeformer", False) and not args.rerender:
            return _run_face_only_codeformer_standalone(cfg=cfg, args=args)
        if args.rerender:
            if specific_moments:
                raise ValueError("--specific-moment is not supported with --rerender.")
            return _run_rerender(cfg=cfg, args=args)
        if getattr(args, "discover", None) is not None:
            ret = _run_discover_standalone(cfg=cfg, args=args)
            if ret != -1:
                return ret
        if not args.url.strip() and not args.skip_download:
            raise ValueError(
                "--url is required unless --rerender, --skip-download, or --discover is used."
            )

        # ---- Stage 1: Download (or use local media)
        print_stage("1. Obtenção de Mídia (Download / Local)")
        t_stage1 = time.time()
        if args.skip_download:
            v_path_str = args.video_path.strip()
            a_path_str = args.audio_path.strip()

            if not v_path_str and not a_path_str:
                raise ValueError(
                    "--skip-download requer ao menos --video-path ou --audio-path (ou use --input)."
                )

            if v_path_str:
                video_path = Path(v_path_str).expanduser().resolve()
                if not video_path.exists():
                    raise FileNotFoundError(f"Video path not found: {video_path}")
            else:
                video_path = None

            if a_path_str:
                audio_path = Path(a_path_str).expanduser().resolve()
                if not audio_path.exists():
                    raise FileNotFoundError(f"Audio path not found: {audio_path}")
            else:
                if video_path:
                    from src.stage_cache import file_digest
                    video_key = file_digest(video_path)[:16]
                    audio_path = cfg.audio_dir / f"{video_path.stem}.{video_key}.wav"
                    if not audio_path.exists():
                        LOGGER.info("Extraindo áudio do vídeo local: %s", video_path.name)
                        run_cmd([
                            "ffmpeg", "-y", "-i", str(video_path),
                            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                            str(audio_path)
                        ], check=True)
                else:
                    raise ValueError("Audio path missing and no video path provided to extract from.")

            # Derive slug: prefer --slug, then stem of input file, then URL
            v_stem = Path(args.video_path).stem if args.video_path.strip() else ""
            if not v_stem and args.audio_path.strip():
                v_stem = Path(args.audio_path).stem
            
            slug = make_slug(
                args.url,
                title=v_stem if not args.slug.strip() else "",
                user_slug=args.slug,
            )
            artifacts.raw_video = video_path
            artifacts.raw_audio = audio_path
            LOGGER.info(
                "Using local media files | video=%s | audio=%s", video_path, audio_path
            )
            timings["1. Obtenção de Mídia"] = round(time.time() - t_stage1, 2)
            print_success("Download", f"Mídia local carregada: [bold cyan]{slug}[/bold cyan]", elapsed=timings["1. Obtenção de Mídia"])
        else:
            temp_slug = make_slug(args.url, title="", user_slug=args.slug)
            if getattr(args, "http_range", False):
                return _run_http_range_pipeline(cfg=cfg, args=args, slug=temp_slug, url=args.url)
            downloader = YouTubeDownloader(
                config=cfg,
                extract_audio_format=args.audio_format,
            )
            dl = downloader.download(url=args.url, slug=temp_slug)

            # Recompute slug using title if user didn't pass one
            slug = make_slug(args.url, title=dl.title, user_slug=args.slug)

            # If slug changed after title discovery, keep existing files and just continue with computed slug.
            artifacts.raw_video = dl.video_path
            artifacts.raw_audio = dl.audio_path
            LOGGER.info(
                "Downloaded media | title=%s | video=%s | audio=%s",
                dl.title,
                dl.video_path,
                dl.audio_path,
            )
            timings["1. Obtenção de Mídia"] = round(time.time() - t_stage1, 2)
            print_success("Download", f"Mídia baixada com sucesso: [bold cyan]{dl.title}[/bold cyan]", elapsed=timings["1. Obtenção de Mídia"])

            if args.download_original_video:
                LOGGER.info(
                    "Download-only mode enabled; stopping after original video download."
                )
                print_timing_table(timings, time.time() - t_pipeline_start)
                return 0

        if specific_moments:
            if args.transcript_json.strip():
                raise ValueError(
                    "--specific-moment cannot be used with --transcript-json because timings "
                    "would refer to a different source interval."
                )

            LOGGER.info(
                "Specific-moment manual mode enabled with %d interval(s).",
                len(specific_moments),
            )
            print_stage("2. Processamento de Momentos Específicos")
            t_spec = time.time()

            release_gpu_memory()
            original_whisper_device = cfg.whisper_device
            original_whisper_compute = cfg.whisper_compute_type
            if cfg.stage1_cpu_only:
                cfg.whisper_device = "cpu"
                cfg.whisper_compute_type = "int8"
                LOGGER.info(
                    "Stage-1 CPU mode enabled for specific-moment transcription."
                )

            try:
                clips: list[ClipCandidate] = []
                render_results = []
                transcript_entries: list[dict] = []
                
                out_path = new_run_dir(_profile_clips_dir(cfg=cfg, slug=slug))

                renderer = None if args.dry_run else ShortsRenderer(cfg)

                for idx, moment in enumerate(specific_moments, start=1):
                    start_s, end_s = moment
                    LOGGER.info(
                        "Applying --specific-moment [%d/%d] | start=%.3fs | end=%.3fs | duration=%.3fs",
                        idx,
                        len(specific_moments),
                        start_s,
                        end_s,
                        end_s - start_s,
                    )
                    print_thread(idx, f"Processando intervalo [{start_s:.1f}s - {end_s:.1f}s] ({end_s - start_s:.1f}s)")

                    trimmed_video, trimmed_audio = _trim_media_to_specific_moment(
                        cfg=cfg,
                        video_path=artifacts.raw_video,
                        slug=slug,
                        moment=moment,
                        audio_format=args.audio_format,
                        interval_index=idx,
                    )

                    transcript_path = (
                        out_path
                        / f"{slug}.specific_moment_{idx:02d}.transcript.json"
                    )
                    transcript = transcribe_audio(
                        config=cfg,
                        audio_path=trimmed_audio,
                        output_json_path=transcript_path,
                        language=args.language,
                    )
                    transcript.source_video = trimmed_video
                    write_json(transcript_path, transcript.to_dict())

                    clip = _build_specific_moment_manual_clip(
                        duration_s=end_s - start_s,
                        slug=slug,
                        index=idx,
                        source_window=moment,
                    )

                    if getattr(args, "enrich_clip", False):
                        LOGGER.info(
                            f"Enriquecendo corte manual {idx:02d} com B-rolls e SFX via Ollama..."
                        )
                        curator = get_curator(cfg)
                        clip = curator.enrich_clip_effects(clip, transcript)
                        curator.unload_model()

                    clips.append(clip)
                    transcript_entries.append(
                        {
                            "index": idx,
                            "source_start": start_s,
                            "source_end": end_s,
                            "source_duration": end_s - start_s,
                            "trimmed_video": str(trimmed_video),
                            "trimmed_audio": str(trimmed_audio),
                            "transcript_json": str(transcript_path),
                        }
                    )

                    if not args.dry_run:
                        render_results.append(
                            renderer.render_single_clip(
                                source_video=trimmed_video,
                                transcript=transcript,
                                clip=clip,
                                output_dir=out_path,
                                slug=slug,
                                index=idx,
                                transcript_json_path=transcript_path,
                            )
                        )

                clips_json = cfg.transcripts_dir / f"{slug}.clips.json"
                save_clip_candidates(clips_json, clips)
                artifacts.clips_json = clips_json

                transcripts_manifest = (
                    cfg.transcripts_dir / f"{slug}.specific_moments.transcripts.json"
                )
                write_json(
                    transcripts_manifest,
                    {
                        "slug": slug,
                        "specific_moments": [
                            {
                                "start": start,
                                "end": end,
                                "duration": end - start,
                            }
                            for start, end in specific_moments
                        ],
                        "entries": transcript_entries,
                    },
                )
                artifacts.transcript_json = transcripts_manifest
                artifacts.raw_video = Path(
                    transcript_entries[0]["trimmed_video"]
                ).resolve()
                artifacts.raw_audio = Path(
                    transcript_entries[0]["trimmed_audio"]
                ).resolve()

                timings["2. Momentos Específicos"] = round(time.time() - t_spec, 2)
                print_cuts_table(clips)
                print_success("Momentos", f"{len(clips)} corte(s) gerado(s) com sucesso!", elapsed=timings["2. Momentos Específicos"])

                if args.dry_run:
                    LOGGER.info("Dry-run enabled; skipping rendering.")
                    print_warning("Dry-Run", "Modo simulação ativado; renderização de vídeo ignorada.")
                    print_timing_table(timings, time.time() - t_pipeline_start)
                    summary = {
                        "status": "ok",
                        "dry_run": True,
                        "profile": int(cfg.pipeline_profile),
                        "specific_moments": [
                            {
                                "start": start,
                                "end": end,
                                "duration": end - start,
                            }
                            for start, end in specific_moments
                        ],
                        "artifacts": artifacts.to_dict(),
                        "clips": [c.to_dict() for c in clips],
                    }
                    print(json.dumps(summary, ensure_ascii=False, indent=2))
                    return 0

                artifacts.rendered_clips = [r.output_path for r in render_results]
                _apply_post_processing_zoom(cfg, render_results)
                upscaled = _run_optional_upscaling(
                    cfg=cfg, render_results=render_results
                )
                timings["3. Renderização"] = round(time.time() - t_spec, 2)
                print_success("Renderer", f"{len(render_results)} corte(s) renderizado(s) com sucesso!")
                print_timing_table(timings, time.time() - t_pipeline_start)
                summary = {
                    "status": "ok",
                    "profile": int(cfg.pipeline_profile),
                    "specific_moments": [
                        {
                            "start": start,
                            "end": end,
                            "duration": end - start,
                        }
                        for start, end in specific_moments
                    ],
                    "artifacts": artifacts.to_dict(),
                    "clips_count": len(clips),
                    "rendered_count": len(render_results),
                    "rendered": [r.to_dict() for r in render_results],
                    "upscaled": upscaled,
                }
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 0
            finally:
                cfg.whisper_device = original_whisper_device
                cfg.whisper_compute_type = original_whisper_compute

        # ---- Stage 2: Stage-1 semantic extraction (CPU + LLM)
        release_gpu_memory()
        original_whisper_device = cfg.whisper_device
        original_whisper_compute = cfg.whisper_compute_type
        if cfg.stage1_cpu_only and not args.transcript_json.strip():
            cfg.whisper_device = "cpu"
            cfg.whisper_compute_type = "int8"
            LOGGER.info(
                "Stage-1 CPU mode enabled for transcription (WHISPER_DEVICE=cpu)."
            )


        # ---- Stage 2: Transcription
        print_stage("2. Transcrição de Áudio (Whisper)")
        t_stage2 = time.time()
        try:
            transcript_path = cfg.transcripts_dir / f"{slug}.transcript.json"
            if args.transcript_json.strip():
                given = Path(args.transcript_json).expanduser().resolve()
                if not given.exists():
                    raise FileNotFoundError(f"Transcript JSON not found: {given}")
                transcript = load_transcript_from_json(given)
                LOGGER.info("Loaded existing transcript JSON: %s", given)
                artifacts.transcript_json = given
                timings["2. Transcrição (Whisper)"] = round(time.time() - t_stage2, 2)
                print_success("Whisper", f"Transcrição carregada do cache: [bold cyan]{given.name}[/bold cyan]", elapsed=timings["2. Transcrição (Whisper)"])
            else:
                transcript = transcribe_audio(
                    config=cfg,
                    audio_path=artifacts.raw_audio,
                    output_json_path=transcript_path,
                    language=args.language,
                    defer_alignment=cfg.selective_alignment and not args.subtitles_only,
                )
                transcript.source_video = artifacts.raw_video
                # update saved transcript with source_video included
                write_json(transcript_path, transcript.to_dict())
                artifacts.transcript_json = transcript_path
                timings["2. Transcrição (Whisper)"] = round(time.time() - t_stage2, 2)
                print_success("Whisper", f"Transcrição concluída: [bold cyan]{len(transcript.segments)}[/bold cyan] segmentos ({transcript.duration:.1f}s)", elapsed=timings["2. Transcrição (Whisper)"])
        finally:
            cfg.whisper_device = original_whisper_device
            cfg.whisper_compute_type = original_whisper_compute

        if getattr(args, "subtitles_only", False):
            LOGGER.info("Modo --subtitles-only ativado. Gerando legendas para o arquivo completo...")
            renderer = ShortsRenderer(cfg)
            builder = renderer._new_caption_builder()
            
            full_duration = transcript.end - transcript.start
            ass_path = cfg.subtitles_dir / f"{slug}.ass"
            
            builder.build(transcript, ass_path, clip_start=0, clip_end=full_duration)
            LOGGER.info("Legendas geradas: %s (e .srt)", ass_path)
            print_success("Legendas", f"Legendas geradas com sucesso: [bold cyan]{ass_path.name}[/bold cyan]")
            
            overlay_path = None
            if cfg.captions_export_overlay_video:
                overlay_path = cfg.clips_dir / f"{slug}.overlay.{cfg.captions_overlay_video_ext}"
                renderer._render_subtitle_overlay_video(
                    subtitle_path=ass_path,
                    output_path=overlay_path,
                    duration_s=full_duration
                )
                LOGGER.info("Vídeo de overlay (transparente) gerado: %s", overlay_path)
                print_success("Legendas", f"Vídeo de overlay gerado: [bold cyan]{overlay_path.name}[/bold cyan]")
            
            timings["3. Legendas"] = round(time.time() - t_stage2, 2)
            print_timing_table(timings, time.time() - t_pipeline_start)
            summary = {
                "status": "ok",
                "mode": "subtitles_only",
                "slug": slug,
                "transcript": str(artifacts.transcript_json),
                "subtitles": str(ass_path),
                "overlay_video": str(overlay_path) if overlay_path else None
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        if transcript.duration <= 0:
            raise RuntimeError("Transcription produced zero duration; cannot continue.")

        # ---- Stage 3: Curation
        print_stage("3. Curadoria Cognitiva de Cortes")
        t_stage3 = time.time()
        clips_json = cfg.transcripts_dir / f"{slug}.clips.json"
        import hashlib
        fingerprint = hashlib.sha256(json.dumps({
            "version": "editorial-v7-staged-story-editor",
            "narrative": [cfg.narrative_edit_enabled, cfg.narrative_montage_enabled,
                          cfg.hook_preview_enabled, cfg.hook_preview_max_seconds,
                          cfg.narrative_context_seconds, cfg.narrative_max_parts],
            "semantic": [cfg.semantic_montage_enabled,cfg.semantic_embedding_model,
                         cfg.semantic_embedding_revision,cfg.semantic_similarity_threshold,cfg.semantic_max_pairs],
            "jev": [cfg.jev_enabled,cfg.jev_model,cfg.jev_confidence_threshold,
                    bool(cfg.typesafe_api_key),cfg.jev_budget_usd,cfg.jev_input_usd_per_million],
            "review": cfg.editorial_review_enabled,
            "review_max": cfg.editorial_review_max_candidates,
            "hook_window": cfg.hook_search_window_seconds,
            "story_window": cfg.story_arc_window_seconds, "transcript": transcript.to_dict(),
            "provider": cfg.curator_provider, "ollama": cfg.ollama_model,
            "google": cfg.google_model_name, "deepseek": cfg.deepseek_model_name,
            "min": cfg.clip_min_seconds, "max": cfg.clip_max_seconds,
            "count": cfg.ollama_clips_count, "score": cfg.virality_min_score,
            "gap": cfg.clip_min_gap_seconds,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        cache_meta = clips_json.with_suffix(".cache.json")
        cache_matches = False
        if cache_meta.exists():
            try:
                cache_matches = json.loads(cache_meta.read_text()).get("fingerprint") == fingerprint
            except (ValueError, OSError):
                pass
        if cfg.stage_cache_enabled and clips_json.exists() and cache_matches and not getattr(args, "force_curate", False):
            LOGGER.info("Clips JSON already exists at %s. Loading...", clips_json)
            clips = load_clip_candidates(clips_json)
            artifacts.clips_json = clips_json
            timings["3. Curadoria Cognitiva"] = round(time.time() - t_stage3, 2)
            print_success("Curadoria", f"Cortes carregados do cache: [bold cyan]{len(clips)}[/bold cyan] candidatos", elapsed=timings["3. Curadoria Cognitiva"])
        else:
            if getattr(args, "jev_curate", False) or cfg.curator_provider in {"jev", "typesafe"}:
                import asyncio
                LOGGER.info("Executando Curadoria Cognitiva Ultra-Rápida em Duas Camadas com o Jev...")
                curator = JevTwoLayerCurator(
                    api_key=cfg.typesafe_api_key or os.environ.get("JEV_API_KEY") or os.environ.get("TYPESAFE_API_KEY"),
                    model=cfg.jev_model,
                )
                segments_dict = [{"start": s.start, "end": s.end, "text": s.text} for s in transcript.segments]
                min_dur = float(args.clip_min_seconds) if getattr(args, "clip_min_seconds", None) is not None else 180.0
                max_dur = float(args.clip_max_seconds) if getattr(args, "clip_max_seconds", None) is not None else 300.0
                max_clips = getattr(args, "max_clips", None) or cfg.ollama_clips_count
                viral_cuts = asyncio.run(
                    curator.curate(
                        segments=segments_dict,
                        min_duration=min_dur,
                        max_duration=max_dur,
                        max_clips=max_clips,
                    )
                )
                cuts_json = cfg.data_dir / f"viral_cuts_{slug}.json"
                video_title = dl.title if "dl" in locals() else slug
                curator.save_viral_cuts_json(viral_cuts, cuts_json, video_id=slug, title=video_title)
                clips = curator.to_clip_candidates(viral_cuts, title_prefix=slug)
                from src.curator import CuratorResult
                curation = CuratorResult(
                    clips=clips,
                    raw_response="",
                    parsed_ok=bool(clips),
                    warning=None if clips else "Nenhum corte aprovado pelo Jev.",
                )
                timings["3. Curadoria (Jev)"] = round(time.time() - t_stage3, 2)
                print_success("Jev", f"Curadoria finalizada: [bold cyan]{len(clips)}[/bold cyan] corte(s) virais selecionados", elapsed=timings["3. Curadoria (Jev)"])
            else:
                curator = get_curator(cfg)
                try:
                    curation = curator.curate(transcript)
                finally:
                    curator.unload_model()
                clips = curation.clips
                timings["3. Curadoria Cognitiva"] = round(time.time() - t_stage3, 2)
                print_success("Curadoria", f"Curadoria finalizada: [bold cyan]{len(clips)}[/bold cyan] corte(s) selecionados", elapsed=timings["3. Curadoria Cognitiva"])

            if not clips:
                diagnostic_dir = new_run_dir(cfg.data_dir / "reviews" / slug)
                write_quality_review(diagnostic_dir, transcript, [], source_video=artifacts.raw_video)
                diagnostic = {
                    "status": "no_candidates", "dry_run": bool(args.dry_run),
                    "message": curation.warning or "Nenhum candidato passou pelos critérios editoriais.",
                    "artifacts": artifacts.to_dict(), "clips_count": 0,
                    "review": str(diagnostic_dir / "review.html"),
                }
                write_json(diagnostic_dir / "diagnostic.json", diagnostic)
                LOGGER.warning("Nenhum corte aprovado: %s | Diagnóstico: %s", diagnostic['message'], diagnostic_dir)
                print_warning("Curadoria", diagnostic["message"])
                print_timing_table(timings, time.time() - t_pipeline_start)
                print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
                return 0

            save_clip_candidates(clips_json, clips)
            write_json(cache_meta, {"fingerprint": fingerprint,
                                    "api_reservations": curator.budget.calls if hasattr(curator, "budget") else []})
            artifacts.clips_json = clips_json

            LOGGER.info(
                "Curated %d clip(s) | parsed_ok=%s | warning=%s",
                len(clips),
                curation.parsed_ok,
                curation.warning or "-",
            )

        print_cuts_table(clips)
        
        if not args.dry_run:
            auto_confirm = getattr(args, "yes", False) or not sys.stdin.isatty()
            if not auto_confirm:
                try:
                    ans = input("Deseja iniciar a renderização de todos os clipes acima? (S/n): ")
                    if ans.strip().lower() == "n":
                        LOGGER.info("Cancelado pelo usuário.")
                        print_warning("Pipeline", "Cancelado pelo usuário.")
                        return 0
                except (EOFError, KeyboardInterrupt):
                    LOGGER.info("Entrada não interativa detectada. Prosseguindo automaticamente com a renderização.")
            else:
                LOGGER.info("Renderização confirmada automaticamente (modo não-interativo ou flag --yes).")

        # explicit stage boundary cleanup before GPU-heavy rendering
        release_gpu_memory()

        # ---- Stage 4: Rendering
        if args.dry_run:
            review_dir = new_run_dir(cfg.data_dir / "reviews" / slug)
            write_quality_review(review_dir, transcript, clips, source_video=artifacts.raw_video)
            LOGGER.info("Local candidate review: %s", review_dir / "review.html")
            LOGGER.info("Dry-run enabled; skipping rendering.")
            print_warning("Dry-Run", "Modo simulação ativado; renderização de vídeo ignorada.")
            print_timing_table(timings, time.time() - t_pipeline_start)
            summary = {
                "status": "ok",
                "dry_run": True,
                "profile": int(cfg.pipeline_profile),
                "artifacts": artifacts.to_dict(),
                "clips": [c.to_dict() for c in clips],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        release_gpu_memory()

        print_stage("4. Renderização 9:16 e Tracking Facial")
        t_stage4 = time.time()

        out_path = new_run_dir(_profile_clips_dir(cfg=cfg, slug=slug))
        if cfg.whisper_alignment and cfg.selective_alignment:
            transcript = WhisperTranscriber(cfg).align_selected(transcript, artifacts.raw_audio, clips)
            # Keep raw ASR and render-ready transcripts separately.
            aligned_path = out_path / f"{slug}.aligned.transcript.json"
            write_json(aligned_path, transcript.to_dict())
            artifacts.transcript_json = aligned_path
            clips = refine_render_clips(clips, transcript, cfg)
            if not clips:
                raise RuntimeError("No complete clips fit duration limits after word-boundary refinement")
        artifacts.transcript_json = out_path / f"{slug}.render.transcript.json"
        write_json(artifacts.transcript_json, transcript.to_dict())
        write_json(out_path / "selected-clips.json", {"clips": [c.to_dict() for c in clips]})
        write_quality_review(out_path, transcript, clips, source_video=artifacts.raw_video)

        renderer = ShortsRenderer(cfg)
        render_results = renderer.render_clips(
            source_video=artifacts.raw_video,
            transcript=transcript,
            clips=clips,
            output_dir=out_path,
            slug=slug,
            transcript_json_path=artifacts.transcript_json,
        )

        artifacts.rendered_clips = [r.output_path for r in render_results]
        write_quality_review(out_path, transcript, clips, render_results, artifacts.raw_video)
        _apply_post_processing_zoom(cfg, render_results)
        upscaled = _run_optional_upscaling(cfg=cfg, render_results=render_results)

        timings["4. Renderização e Tracking"] = round(time.time() - t_stage4, 2)
        print_success("Renderer", f"[bold cyan]{len(render_results)}[/bold cyan] shorts renderizados com sucesso!", elapsed=timings["4. Renderização e Tracking"])

        t_total = time.time() - t_pipeline_start
        print_timing_table(timings, t_total)

        summary = {
            "status": "ok",
            "profile": int(cfg.pipeline_profile),
            "specific_moments": [],
            "artifacts": artifacts.to_dict(),
            "clips_count": len(clips),
            "rendered_count": len(render_results),
            "rendered": [r.to_dict() for r in render_results],
            "upscaled": upscaled,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    except KeyboardInterrupt:
        LOGGER.warning("Interrupted by user.")
        print_warning("Pipeline", "Execução interrompida pelo usuário.")
        return 130
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        print_error("Pipeline", f"Falha na execução: {exc}")
        error_payload = {
            "status": "error",
            "message": str(exc),
            "artifacts": artifacts.to_dict() if artifacts else None,
        }
        print(json.dumps(error_payload, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
