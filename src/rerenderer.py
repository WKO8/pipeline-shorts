from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import List, Optional

import pysubs2

from src.config import AppConfig
from src.models import ClipCandidate, TrackingPath, TrackingPoint, Transcript
from src.renderer import RenderResult, ShortsRenderer
from src.post_processor import apply_cinematic_zoom

LOGGER = logging.getLogger("pipeline.rerenderer")


def _resolve_existing_file(path_or_name: str, search_roots: list[Path]) -> Path:
    raw = (path_or_name or "").strip()
    if not raw:
        raise FileNotFoundError("Empty file path provided.")
    direct = Path(raw).expanduser()
    if direct.is_file():
        return direct.resolve()

    checked: list[Path] = [direct]
    for root in search_roots:
        if not root.exists():
            continue
        candidate = (root / raw).resolve()
        checked.append(candidate)
        if candidate.is_file():
            return candidate
        matches = list(root.rglob(raw))
        if matches:
            files = [m for m in matches if m.is_file()]
            if files:
                return files[0].resolve()

    options = "\n".join(str(p) for p in checked[:8])
    raise FileNotFoundError(
        f"Could not find file '{raw}'. Checked locations:\n{options}"
    )


def subtract_windows(
    base_windows: list[tuple[float, float]],
    minus_windows: list[tuple[float, float]],
    gap: float = 0.06,
) -> list[tuple[float, float]]:
    if not base_windows:
        return []
    if not minus_windows:
        return list(base_windows)
    cutouts = sorted(
        [(max(0.0, s - gap), e + gap) for s, e in minus_windows if e > s],
        key=lambda x: x[0],
    )
    result: list[tuple[float, float]] = []
    for start, end in base_windows:
        if end <= start:
            continue
        segments = [(start, end)]
        for cut_start, cut_end in cutouts:
            next_segments: list[tuple[float, float]] = []
            for seg_start, seg_end in segments:
                if cut_end <= seg_start or cut_start >= seg_end:
                    next_segments.append((seg_start, seg_end))
                    continue
                if cut_start > seg_start:
                    next_segments.append((seg_start, cut_start))
                if cut_end < seg_end:
                    next_segments.append((cut_end, seg_end))
            segments = next_segments
            if not segments:
                break
        for seg_start, seg_end in segments:
            if seg_end > seg_start:
                result.append((seg_start, seg_end))
    if not result:
        return []
    merged: list[tuple[float, float]] = [result[0]]
    for start, end in result[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 0.04:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def apply_duo_disable_windows(
    tracking: TrackingPath,
    disable_windows: list[tuple[float, float]],
    clip_duration: float,
    pad_seconds: float,
) -> None:
    if not disable_windows:
        return
    pad = max(0.0, float(pad_seconds))
    current = list(tracking.active_speaker_multi_face_windows or [])
    tracking.active_speaker_multi_face_windows = subtract_windows(
        base_windows=current,
        minus_windows=disable_windows,
        gap=pad,
    )
    total = sum(
        max(0.0, end - start)
        for start, end in tracking.active_speaker_multi_face_windows
    )
    if clip_duration > 0:
        tracking.active_speaker_multi_face_ratio = float(total / clip_duration)
    else:
        tracking.active_speaker_multi_face_ratio = 0.0


def point_for_time(
    points: list[TrackingPoint], t: float, prefer_before: bool
) -> Optional[TrackingPoint]:
    if not points:
        return None
    if prefer_before:
        candidate: Optional[TrackingPoint] = None
        for point in points:
            if point.t <= t:
                candidate = point
            else:
                break
        return candidate
    for point in points:
        if point.t >= t:
            return point
    return None


def stabilize_tracking_points_in_windows(
    points: list[TrackingPoint],
    windows: list[tuple[float, float]],
    max_x: float,
    max_y: float,
    pad_seconds: float,
) -> list[TrackingPoint]:
    if not points or not windows:
        return points

    ordered = sorted(points, key=lambda p: p.t)
    pad = max(0.0, float(pad_seconds))
    for start, end in windows:
        w_start = max(0.0, float(start) - pad)
        w_end = max(w_start, float(end) + pad)
        before = point_for_time(ordered, w_start, prefer_before=True)
        after = point_for_time(ordered, w_end, prefer_before=False)

        for idx, point in enumerate(ordered):
            if point.t < w_start or point.t > w_end:
                continue

            if before is not None and after is not None and after.t > before.t:
                alpha = (point.t - before.t) / (after.t - before.t)
                alpha = max(0.0, min(1.0, alpha))
                x = before.x + ((after.x - before.x) * alpha)
                y = before.y + ((after.y - before.y) * alpha)
            elif before is not None:
                x = before.x
                y = before.y
            elif after is not None:
                x = after.x
                y = after.y
            else:
                continue

            ordered[idx] = TrackingPoint(
                t=point.t,
                x=max(0.0, min(max_x, float(x))),
                y=max(0.0, min(max_y, float(y))),
            )

    return ordered


def apply_focus_fix_windows(
    tracking: TrackingPath,
    focus_windows: list[tuple[float, float]],
    clip_duration: float,
    pad_seconds: float,
) -> None:
    if not focus_windows:
        return
    normalized = []
    for start, end in focus_windows:
        s = max(0.0, min(float(start), clip_duration))
        e = max(0.0, min(float(end), clip_duration))
        if e > s:
            normalized.append((s, e))
    if not normalized:
        return

    max_x = max(0.0, float(tracking.source_width - tracking.crop_width))
    max_y = max(0.0, float(tracking.source_height - tracking.crop_height))

    tracking.points = stabilize_tracking_points_in_windows(
        points=tracking.points,
        windows=normalized,
        max_x=max_x,
        max_y=max_y,
        pad_seconds=pad_seconds,
    )
    tracking.secondary_points = stabilize_tracking_points_in_windows(
        points=tracking.secondary_points,
        windows=normalized,
        max_x=max_x,
        max_y=max_y,
        pad_seconds=pad_seconds,
    )
    tracking.duo_left_points = stabilize_tracking_points_in_windows(
        points=tracking.duo_left_points,
        windows=normalized,
        max_x=max_x,
        max_y=max_y,
        pad_seconds=pad_seconds,
    )
    tracking.duo_right_points = stabilize_tracking_points_in_windows(
        points=tracking.duo_right_points,
        windows=normalized,
        max_x=max_x,
        max_y=max_y,
        pad_seconds=pad_seconds,
    )


def slug_variants_for_legacy_lookup(raw_slug: str) -> list[str]:
    base = (raw_slug or "").strip()
    if not base:
        return []

    candidates: list[str] = []

    def add(value: str) -> None:
        v = value.strip("-_")
        if v and v not in candidates:
            candidates.append(v)

    add(base)
    add(re.sub(r"_teste_v\d+$", "", base))
    add(re.sub(r"_teste$", "", base))

    hyphen_parts = [p for p in base.split("-") if p]
    for idx in range(1, len(hyphen_parts)):
        add("-".join(hyphen_parts[idx:]))

    underscore_parts = [p for p in base.split("_") if p]
    for idx in range(1, len(underscore_parts)):
        add("_".join(underscore_parts[idx:]))

    return candidates


def resolve_legacy_transcript_bundle(
    cfg: AppConfig,
    legacy_slug: str,
) -> tuple[Path, Path, str]:
    variants = slug_variants_for_legacy_lookup(legacy_slug)
    matches: list[tuple[Path, Path, str]] = []
    for variant in variants:
        transcript_json = cfg.transcripts_dir / f"{variant}.transcript.json"
        clips_json = cfg.transcripts_dir / f"{variant}.clips.json"
        if transcript_json.exists() and clips_json.exists():
            matches.append((transcript_json.resolve(), clips_json.resolve(), variant))

    if not matches:
        attempted = ", ".join(variants[:10]) or legacy_slug
        raise FileNotFoundError(
            "Transcript/Clips JSON not found for legacy rerender. "
            f"Tried variants: {attempted}"
        )

    matches.sort(key=lambda item: len(item[2]), reverse=True)
    return matches[0]


def load_rerender_context(cfg: AppConfig, clip_path: Path) -> dict:
    clip_stem = clip_path.stem
    exact_meta_candidates = sorted(
        {path.resolve() for path in cfg.subtitles_dir.rglob(f"{clip_stem}.meta.json")}
    )
    if len(exact_meta_candidates) == 1:
        meta_candidates = exact_meta_candidates
    elif len(exact_meta_candidates) > 1:
        meta_candidates = exact_meta_candidates
    else:
        meta_candidates = sorted(
            {path.resolve() for path in cfg.subtitles_dir.rglob(f"*{clip_stem}*.meta.json")}
        )

    if len(meta_candidates) > 1:
        matching = []
        for candidate in meta_candidates:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if Path(data.get("clip_path", "")).resolve() == clip_path.resolve():
                    matching.append(candidate)
            except (OSError, ValueError):
                continue
        if len(matching) != 1:
            raise ValueError("Ambiguous render metadata: choose the original clip path for this generation")
        meta_candidates = matching
    if meta_candidates:
        meta_path = meta_candidates[0]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        tracking_path = meta.get("tracking_path")
        resolved_tracking = (
            Path(tracking_path).resolve()
            if tracking_path and Path(tracking_path).exists()
            else None
        )
        clip_data = meta.get("clip") or {}
        clip = ClipCandidate(
            start=float(clip_data.get("start", 0.0)),
            end=float(clip_data.get("end", 0.0)),
            score=float(clip_data.get("score", 0.0)),
            reason=str(clip_data.get("reason", "")),
            title=str(clip_data.get("title", "")),
            layout_mode=str(clip_data.get("layout_mode", "single")),
            b_roll_overlay=str(clip_data.get("b_roll_overlay", "")),
        )
        return {
            "source_video": Path(meta["source_video"]).resolve(),
            "transcript_json": Path(meta["transcript_json"]).resolve(),
            "tracking_path": resolved_tracking,
            "clip": clip,
            "slug": meta.get("slug", "video"),
            "index": int(meta.get("index", 1)),
            "subtitle_path": meta.get("subtitle_path"),
            "subtitle_overlay_path": meta.get("subtitle_overlay_path"),
        }

    match = re.match(r"^(.*?)(?:_clip_(\d+))?$", clip_stem)
    slug = match.group(1) if match else clip_stem
    index = int(match.group(2)) if match and match.group(2) else 1
    transcript_json, clips_json, canonical_slug = resolve_legacy_transcript_bundle(
        cfg=cfg,
        legacy_slug=slug,
    )
    clips_data = json.loads(clips_json.read_text(encoding="utf-8"))
    clip_list = clips_data.get("clips", [])
    if not clip_list:
        raise ValueError(f"No clips found in {clips_json}")
    selected_clip_idx = min(max(index - 1, 0), len(clip_list) - 1)
    clip_dict = clip_list[selected_clip_idx]
    clip = ClipCandidate(
        start=float(clip_dict.get("start", 0.0)),
        end=float(clip_dict.get("end", 0.0)),
        score=float(clip_dict.get("score", 0.0)),
        reason=str(clip_dict.get("reason", "")),
        title=str(clip_dict.get("title", "")),
        layout_mode=str(clip_dict.get("layout_mode", "single")),
        b_roll_overlay=str(clip_dict.get("b_roll_overlay", "")),
    )

    t_data = json.loads(transcript_json.read_text(encoding="utf-8"))
    source_video_raw = t_data.get("source_video")
    if source_video_raw and Path(source_video_raw).exists():
        source_video = Path(source_video_raw).resolve()
    else:
        downloads = list((cfg.data_dir / "downloads").rglob(f"{canonical_slug}*.mp4"))
        if not downloads:
            downloads = list((cfg.data_dir / "downloads").rglob("*.mp4"))
        if not downloads:
            raise FileNotFoundError(
                f"Source video not found for slug '{canonical_slug}'"
            )
        source_video = downloads[0].resolve()

    return {
        "source_video": source_video,
        "transcript_json": transcript_json.resolve(),
        "tracking_path": None,
        "clip": clip,
        "slug": canonical_slug,
        "index": index,
        "subtitle_path": None,
        "subtitle_overlay_path": None,
    }


def overlay_dialogue_events(subs: pysubs2.SSAFile) -> list[pysubs2.SSAEvent]:
    return [
        ev
        for ev in subs.events
        if ev.type == "Dialogue"
        and not ev.text.startswith("m ")
        and not ev.text.startswith("{\\p1}")
    ]


def load_overlay_text_source(path: Path) -> list[str]:
    ext = path.suffix.lower()
    if ext == ".txt":
        raw = path.read_text(encoding="utf-8")
        return [line.strip() for line in raw.splitlines() if line.strip()]
    if ext == ".ass":
        source_subs = pysubs2.load(str(path), encoding="utf-8")
        events = overlay_dialogue_events(source_subs)
        return [ev.text for ev in events]
    raise ValueError(
        f"Unsupported overlay text source format: {path.suffix}. Use .txt or .ass."
    )


def probe_subtitle_duration_seconds(subtitle_path: Path) -> float:
    subs = pysubs2.load(str(subtitle_path), encoding="utf-8")
    if not subs.events:
        return 0.0
    return max(0.0, max(ev.end for ev in subs.events) / 1000.0)


def load_overlay_context_by_subtitle(
    cfg: AppConfig, subtitle_path: Path
) -> Optional[dict]:
    exact = sorted(
        {
            path.resolve()
            for path in cfg.subtitles_dir.rglob(f"{subtitle_path.stem}.meta.json")
        }
    )
    if exact:
        return load_rerender_context(cfg, exact[0].with_suffix(""))
    return None


def normalize_subtitle_text(value: str) -> str:
    clean = re.sub(r"\{[^}]*\}", "", value or "")
    clean = re.sub(r"\\[Nn]", " ", clean)
    clean = unicodedata.normalize("NFKD", clean)
    clean = "".join(ch for ch in clean if not unicodedata.combining(ch))
    clean = clean.lower()
    return re.sub(r"[^\w]+", "", clean)


def build_monotonic_text_mapping(
    source_texts: list[str], target_texts: list[str]
) -> list[int]:
    src_norm = [normalize_subtitle_text(t) for t in source_texts]
    tgt_norm = [normalize_subtitle_text(t) for t in target_texts]
    sm = difflib.SequenceMatcher(a=src_norm, b=tgt_norm)
    mapped: dict[int, int] = {}
    for block in sm.get_matching_blocks():
        for off in range(block.size):
            mapped[block.b + off] = block.a + off

    m = len(source_texts)
    last_src = 0
    mapping: list[int] = []
    for tgt_idx in range(len(target_texts)):
        if tgt_idx in mapped:
            last_src = mapped[tgt_idx]
        else:
            last_src = min(m - 1, last_src)
        mapping.append(last_src)
    return mapping


def run_overlay_text_fix(cfg: AppConfig, args: argparse.Namespace) -> int:
    if not args.subtitles.strip():
        raise ValueError("--overlay-text-fix requires --subtitles")

    source_subtitle_path = _resolve_existing_file(
        args.subtitles,
        search_roots=[
            cfg.subtitles_dir,
            cfg.temp_dir,
            cfg.project_root / "data",
            cfg.clips_dir,
        ],
    )

    clip_path: Optional[Path] = None
    rerender_ctx: Optional[dict] = None
    if args.clipe.strip():
        clip_path = _resolve_existing_file(
            args.clipe,
            search_roots=[
                cfg.clips_dir,
                cfg.project_root / "data" / "testes",
                cfg.project_root / "data",
            ],
        )
        rerender_ctx = load_rerender_context(cfg, clip_path)
    else:
        rerender_ctx = load_overlay_context_by_subtitle(cfg, source_subtitle_path)
        if rerender_ctx is not None:
            ctx_clip_path = rerender_ctx.get("clip_path")
            if ctx_clip_path is not None and Path(ctx_clip_path).exists():
                clip_path = Path(ctx_clip_path)

    subtitle_path = source_subtitle_path
    if args.overlay_text_source.strip() and rerender_ctx is not None:
        timing_subtitle = rerender_ctx.get("subtitle_path")
        if timing_subtitle is not None and Path(timing_subtitle).exists():
            subtitle_path = Path(timing_subtitle)

    overlay_text_source = source_subtitle_path
    if args.overlay_text_source.strip():
        overlay_text_source = _resolve_existing_file(
            args.overlay_text_source,
            search_roots=[
                cfg.subtitles_dir,
                cfg.temp_dir,
                cfg.project_root / "data",
                source_subtitle_path.parent,
            ],
        )

    if args.overlay_output.strip():
        overlay_output_path = Path(args.overlay_output).expanduser()
        if not overlay_output_path.is_absolute():
            overlay_output_path = (cfg.project_root / overlay_output_path).resolve()
        else:
            overlay_output_path = overlay_output_path.resolve()
    else:
        overlay_output_path = (
            rerender_ctx.get("subtitle_overlay_path") if rerender_ctx else None
        )
        if overlay_output_path is None:
            overlay_output_path = subtitle_path.with_suffix(
                f".overlay.{cfg.captions_overlay_video_ext}"
            )
        else:
            overlay_output_path = Path(overlay_output_path)

    subs = pysubs2.load(str(subtitle_path), encoding="utf-8")
    target_events = overlay_dialogue_events(subs)
    target_original_texts = [event.text for event in target_events]

    source_original_texts: Optional[list[str]] = None
    replacement_texts = load_overlay_text_source(overlay_text_source)
    if overlay_text_source.suffix.lower() == ".ass":
        source_original_texts = list(replacement_texts)
    elif source_subtitle_path is not None and source_subtitle_path.exists():
        source_subs = pysubs2.load(str(source_subtitle_path), encoding="utf-8")
        source_events = overlay_dialogue_events(source_subs)
        source_original_texts = [event.text for event in source_events]

    expected = len(target_events)
    received = len(replacement_texts)

    mapping_strategy = "direct"
    if expected == received:
        for idx, event in enumerate(target_events):
            event.text = replacement_texts[idx]
    else:
        if not source_original_texts:
            raise ValueError(
                "overlay-text-source does not match timing subtitle event count and no source "
                "subtitle was provided for mapping. Pass --subtitles with the source ASS used "
                "to generate the text file."
            )
        if len(source_original_texts) != received:
            raise ValueError(
                "overlay-text-source does not match source subtitle event count: "
                f"source has {len(source_original_texts)} events, text source has {received}."
            )
        mapping = build_monotonic_text_mapping(
            source_texts=source_original_texts,
            target_texts=target_original_texts,
        )
        if len(mapping) != expected:
            raise RuntimeError(
                "Could not build subtitle mapping for overlay timing preservation."
            )
        for idx, event in enumerate(target_events):
            src_idx = max(0, min(received - 1, mapping[idx]))
            event.text = replacement_texts[src_idx]
        mapping_strategy = "monotonic_text_alignment"

    overlay_subtitle_path = cfg.temp_dir / f"{subtitle_path.stem}.overlayfix.ass"
    subs.save(str(overlay_subtitle_path), encoding="utf-8")

    renderer = ShortsRenderer(cfg)
    jump_remapped = False
    source_video: Optional[Path] = None
    clip_candidate: Optional[ClipCandidate] = None

    if rerender_ctx is not None:
        sv = rerender_ctx.get("source_video")
        if sv is not None and Path(sv).exists():
            source_video = Path(sv)
        cc = rerender_ctx.get("clip")
        if isinstance(cc, ClipCandidate):
            clip_candidate = cc

    metadata_used_jump_cuts = False
    if rerender_ctx is not None:
        meta_sub = rerender_ctx.get("subtitle_path")
        if meta_sub is not None:
            meta_sub_stem = Path(meta_sub).stem
            if meta_sub_stem.endswith(".jump"):
                metadata_used_jump_cuts = True

    if (
        (cfg.silence_jump_cuts_enabled or metadata_used_jump_cuts)
        and source_video is not None
        and clip_candidate is not None
    ):
        jump_keep_windows = renderer._build_jump_keep_windows(
            source_video=source_video,
            clip=clip_candidate,
        )
        if jump_keep_windows:
            remapped_path = cfg.temp_dir / f"{subtitle_path.stem}.overlayfix.jump.ass"
            remapped_count = renderer._remap_subtitle_to_jump_cuts(
                source_subtitle_path=overlay_subtitle_path,
                target_subtitle_path=remapped_path,
                keep_windows=jump_keep_windows,
            )
            if remapped_count > 0:
                overlay_subtitle_path = remapped_path
                jump_remapped = True
                LOGGER.info(
                    "Remapped %d subtitle events for jump cuts in overlay-text-fix.",
                    remapped_count,
                )

    if clip_path is not None and clip_path.exists():
        duration_s = renderer._probe_media_duration(clip_path)
    else:
        duration_s = probe_subtitle_duration_seconds(overlay_subtitle_path)
    renderer._render_subtitle_overlay_video(
        subtitle_path=overlay_subtitle_path,
        output_path=overlay_output_path,
        duration_s=duration_s,
    )

    summary = {
        "status": "ok",
        "mode": "overlay_text_fix",
        "clip_path": str(clip_path) if clip_path is not None else None,
        "source_subtitle_path": str(source_subtitle_path),
        "timing_basis_subtitle_path": str(subtitle_path),
        "render_subtitle_path": str(overlay_subtitle_path),
        "overlay_text_source": str(overlay_text_source),
        "overlay_output_path": str(overlay_output_path),
        "subtitle_events_updated": expected,
        "source_text_events": received,
        "mapping_strategy": mapping_strategy,
        "jump_cut_remapped": jump_remapped,
        "timing_changed": jump_remapped,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _parse_window_ranges(raw: str) -> list[tuple[float, float]]:
    from src.utils import hhmmss_to_seconds
    raw = (raw or "").strip()
    if not raw:
        return []
    result = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        parts = token.split("-")
        if len(parts) != 2:
            continue
        try:
            start = hhmmss_to_seconds(parts[0])
            end = hhmmss_to_seconds(parts[1])
            if end > start:
                result.append((start, end))
        except Exception:
            continue
    return result


def run_rerender(cfg: AppConfig, args: argparse.Namespace) -> int:
    from src.models import Transcript

    if not args.subtitles.strip():
        raise ValueError("--rerender requires --subtitles")
    if not args.clipe.strip():
        raise ValueError("--rerender requires --clipe/--clip")

    clip_path = _resolve_existing_file(
        args.clipe,
        search_roots=[
            cfg.clips_dir,
            cfg.project_root / "data" / "testes",
            cfg.project_root / "data",
        ],
    )
    rerender_ctx = load_rerender_context(cfg, clip_path)
    transcript_json = rerender_ctx["transcript_json"]
    
    # Load transcript from JSON
    t_payload = json.loads(transcript_json.read_text(encoding="utf-8"))
    transcript = Transcript.from_dict(t_payload) if hasattr(Transcript, "from_dict") else Transcript(
        language=t_payload.get("language", "pt"),
        source_audio=Path(t_payload.get("source_audio")) if t_payload.get("source_audio") else None,
        source_video=Path(t_payload.get("source_video")) if t_payload.get("source_video") else None,
        segments=[]
    )
    
    subtitle_path = _resolve_existing_file(
        args.subtitles,
        search_roots=[
            cfg.subtitles_dir,
            cfg.temp_dir,
            clip_path.parent,
            cfg.project_root / "data",
        ],
    )

    source_video: Path = rerender_ctx["source_video"]
    if not source_video.exists():
        raise FileNotFoundError(f"Source video not found for rerender: {source_video}")

    if not args.rerender_allow_jump_cuts and cfg.silence_jump_cuts_enabled:
        LOGGER.info("Rerender mode: disabling silence jump cuts to preserve timing.")
        cfg.silence_jump_cuts_enabled = False

    renderer = ShortsRenderer(cfg)
    tracking_override: Optional[TrackingPath] = None
    tracking_path: Optional[Path] = rerender_ctx.get("tracking_path")
    if args.retrack:
        tracking_override = renderer._new_tracker().track_clip(
            video_path=source_video,
            start_s=rerender_ctx["clip"].start,
            end_s=rerender_ctx["clip"].end,
            target_w=cfg.output_width,
            target_h=cfg.output_height,
            speaker_windows=None,
        )
    elif tracking_path is not None and tracking_path.exists():
        tracking_payload = json.loads(tracking_path.read_text(encoding="utf-8"))
        tracking_override = TrackingPath.from_dict(tracking_payload)

    duo_disable_windows = _parse_window_ranges(args.duo_disable_windows)
    focus_fix_windows = _parse_window_ranges(args.focus_fix_windows)
    single_disable_windows = _parse_window_ranges(args.single_disable_windows)

    if args.rerender_full_frame:
        if tracking_override is None:
            tracking_override = renderer._new_tracker().track_clip(
                video_path=source_video,
                start_s=rerender_ctx["clip"].start,
                end_s=rerender_ctx["clip"].end,
                target_w=cfg.output_width,
                target_h=cfg.output_height,
            )
        cfg.fit_scene_full_frame_mode = True
        cfg.force_single_layout = True
        cfg.zoom_per_frame = 0.0
        cfg.zoom_max_scale = 1.0
        cfg.emphasis_zoom_scale = 1.0
        cfg.emphasis_zoom_max_events = 0
        cfg.topic_jump_zoom_scale = 1.0
        cfg.topic_jump_max_events = 0
        cfg.static_pulse_zoom_scale = 1.0
        tracking_override.fit_scene_mode = True
        tracking_override.active_speaker_multi_face_windows = []
        tracking_override.active_speaker_single_windows = []
        tracking_override.active_speaker_multi_face_ratio = 0.0
        rerender_ctx["clip"].layout_mode = "single"
        LOGGER.info("Rerender full-frame mode enabled (single crop disabled, no zoom).")

    if single_disable_windows:
        if tracking_override is None:
            tracking_override = renderer._new_tracker().track_clip(
                video_path=source_video,
                start_s=rerender_ctx["clip"].start,
                end_s=rerender_ctx["clip"].end,
                target_w=cfg.output_width,
                target_h=cfg.output_height,
            )
        clip_duration = max(0.001, float(rerender_ctx["clip"].duration))
        pad = max(0.0, float(args.single_disable_pad))
        disable_windows: list[tuple[float, float]] = []
        for start, end in single_disable_windows:
            s = max(0.0, min(clip_duration, float(start) - pad))
            e = max(0.0, min(clip_duration, float(end) + pad))
            if e > s:
                disable_windows.append((s, e))
        disable_windows.sort(key=lambda x: x[0])

        single_crop_windows = subtract_windows(
            base_windows=[(0.0, clip_duration)],
            minus_windows=disable_windows,
            gap=0.0,
        )

        cfg.fit_scene_full_frame_mode = True
        tracking_override.fit_scene_mode = True
        tracking_override.active_speaker_single_windows = single_crop_windows
        apply_duo_disable_windows(
            tracking=tracking_override,
            disable_windows=disable_windows,
            clip_duration=clip_duration,
            pad_seconds=0.0,
        )

    if duo_disable_windows:
        if tracking_override is None:
            tracking_override = renderer._new_tracker().track_clip(
                video_path=source_video,
                start_s=rerender_ctx["clip"].start,
                end_s=rerender_ctx["clip"].end,
                target_w=cfg.output_width,
                target_h=cfg.output_height,
            )
        apply_duo_disable_windows(
            tracking=tracking_override,
            disable_windows=duo_disable_windows,
            clip_duration=max(0.001, float(rerender_ctx["clip"].duration)),
            pad_seconds=args.duo_disable_pad,
        )

    if focus_fix_windows:
        if tracking_override is None:
            tracking_override = renderer._new_tracker().track_clip(
                video_path=source_video,
                start_s=rerender_ctx["clip"].start,
                end_s=rerender_ctx["clip"].end,
                target_w=cfg.output_width,
                target_h=cfg.output_height,
            )
        apply_focus_fix_windows(
            tracking=tracking_override,
            focus_windows=focus_fix_windows,
            clip_duration=max(0.001, float(rerender_ctx["clip"].duration)),
            pad_seconds=args.focus_fix_pad,
        )

    result = renderer.render_single_clip(
        source_video=source_video,
        transcript=transcript,
        clip=rerender_ctx["clip"],
        output_dir=clip_path.parent,
        slug=rerender_ctx["slug"],
        index=rerender_ctx["index"],
        transcript_json_path=transcript_json,
        subtitle_override_path=subtitle_path,
        tracking_override=tracking_override,
        output_path_override=clip_path,
    )

    summary = {
        "status": "ok",
        "mode": "rerender",
        "profile": int(cfg.pipeline_profile),
        "clip_path": str(result.output_path),
        "subtitle_used": str(subtitle_path),
        "crop_meta_path": str(result.crop_meta_path) if result.crop_meta_path else None,
        "duo_disable_windows": duo_disable_windows,
        "duo_disable_pad": float(args.duo_disable_pad),
        "focus_fix_windows": focus_fix_windows,
        "focus_fix_pad": float(args.focus_fix_pad),
        "rerender_full_frame": bool(args.rerender_full_frame),
        "single_disable_windows": single_disable_windows,
        "single_disable_pad": float(args.single_disable_pad),
        "retrack": bool(args.retrack),
    }
    
    layout = getattr(rerender_ctx["clip"], "layout_mode", "single") if rerender_ctx.get("clip") else "single"
    if cfg.cinematic_zoom and layout not in ("duo_split", "reaction_split"):
        zoomed_path = clip_path.with_stem(clip_path.stem + "_zoomed")
        try:
            apply_cinematic_zoom(
                clip_path,
                zoomed_path,
                tracking_points=result.tracking_data.points if result.tracking_data else None,
                eye_line_ratio=cfg.tracking_eye_line_ratio,
            )
            import shutil
            from .utils import preserve_existing
            preserve_existing(clip_path)
            shutil.copy2(zoomed_path, clip_path)
        except Exception as e:
            LOGGER.error(f"Cinematic zoom error: {e}")
            if zoomed_path.exists():
                LOGGER.info("Preserved zoom output: %s", zoomed_path)

    summary["clip_path"] = str(result.output_path)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
