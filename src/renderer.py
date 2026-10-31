from __future__ import annotations

import copy
import logging
import os
import re
import shutil
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

import pysubs2

from .captions import BrainRotCaptionBuilder, KineticCaptionStyle
from .config import AppConfig
from .models import ClipCandidate, ContextualOverlay, Transcript
from .models import TrackingPath, TrackingPoint
from .tracking import SpeakerTracker
from .transcriber import release_gpu_memory
from .utils import ensure_dir, ensure_parent_dir, run_cmd, write_json

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class RenderResult:
    clip: ClipCandidate
    output_path: Path
    subtitle_path: Optional[Path] = None
    subtitle_overlay_path: Optional[Path] = None
    crop_meta_path: Optional[Path] = None
    tracking_data: Optional[TrackingPath] = None
    zoom_applied: bool = False

    def to_dict(self) -> dict:
        return {
            "clip": self.clip.to_dict(),
            "output_path": str(self.output_path),
            "subtitle_path": str(self.subtitle_path) if self.subtitle_path else None,
            "subtitle_overlay_path": (
                str(self.subtitle_overlay_path) if self.subtitle_overlay_path else None
        ),
            "crop_meta_path": str(self.crop_meta_path) if self.crop_meta_path else None,
        }


@dataclass(slots=True)
class ResolvedContextualOverlay:
    path: Path
    start_rel: float
    end_rel: float
    y_offset: int
    scale: float
    max_width: int


class ShortsRenderer:
    """
    Native 9:16 renderer:
    - dynamic face-aware crop path,
    - kinetic word-by-word captions,
    - NVENC encoding.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._caption_style = KineticCaptionStyle.from_config(config)
        if int(config.pipeline_profile) == 2:
            self._caption_style.letter_spacing = -1.2

        LOGGER.info(
            "Caption style from config: font=%s size=%s color=%s stroke_color=%s stroke_width=%s force_upper=%s",
            self._caption_style.font_name,
            self._caption_style.font_size,
            self._caption_style.primary_color,
            self._caption_style.outline_color,
            self._caption_style.outline,
            self._caption_style.force_uppercase,
        )
        self._tracker_kwargs = dict(
            rolling_window_frames=config.tracking_window_frames,
            ease_alpha=config.tracking_ease_alpha,
            eye_line_ratio=config.tracking_eye_line_ratio,
            confidence_threshold=config.tracking_confidence_threshold,
            focus_min_width_ratio=config.tracking_focus_min_width_ratio,
            speaking_mouth_open_threshold=config.tracking_speaking_mouth_open_threshold,
            jitter_deadzone_px=config.tracking_jitter_deadzone_px,
            max_pan_speed_px_per_s=config.tracking_max_pan_speed_px_per_s,
            multiface_window_min_seconds=config.multiface_window_min_seconds,
            multiface_window_merge_gap_seconds=config.multiface_window_merge_gap_seconds,
            multiface_window_padding_seconds=config.multiface_window_padding_seconds,
            single_window_min_seconds=config.single_window_min_seconds,
            single_window_merge_gap_seconds=config.single_window_merge_gap_seconds,
            single_window_padding_seconds=config.single_window_padding_seconds,
            force_single_layout=config.force_single_layout,
            speaker_lock_priority_enabled=config.speaker_lock_priority_enabled,
            speaker_interrupt_hold_seconds=config.speaker_interrupt_hold_seconds,
            speaker_switch_min_seconds=config.speaker_switch_min_seconds,
            ultra_crop_enabled=config.ultra_crop_enabled,
            ultra_crop_face_width_factor=config.ultra_crop_face_width_factor,
            ultra_crop_min_height_ratio=config.ultra_crop_min_height_ratio,
        )
        self._ensure_nvenc_encoder()

    def render_clips(
        self,
        source_video: str | Path,
        transcript: Transcript,
        clips: List[ClipCandidate],
        output_dir: str | Path | None = None,
        slug: str = "video",
        transcript_json_path: str | Path | None = None,
    ) -> List[RenderResult]:
        source_video = Path(source_video).resolve()
        if not source_video.exists():
            raise FileNotFoundError(f"Source video not found: {source_video}")
        out_dir = Path(output_dir).resolve() if output_dir else self.config.clips_dir
        ensure_dir(out_dir)
        ensure_dir(self.config.temp_dir)

        results: list[RenderResult] = []
        workers = max(1, min(self.config.render_parallel_jobs, len(clips)))
        if workers <= 1 or len(clips) <= 1:
            for idx, clip in enumerate(clips, start=1):
                results.append(
                    self.render_single_clip(
                        source_video=source_video,
                        transcript=transcript,
                        clip=clip,
                        output_dir=out_dir,
                        slug=slug,
                        index=idx,
                        transcript_json_path=transcript_json_path,
                )
            )
                release_gpu_memory()
            return results

        futures = {}
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="render-clip"
        ) as pool:
            for idx, clip in enumerate(clips, start=1):
                futures[
                    pool.submit(
                        self._render_single_isolated,
                        source_video=source_video,
                        transcript=transcript,
                        clip=clip,
                        output_dir=out_dir,
                        slug=slug,
                        index=idx,
                        transcript_json_path=transcript_json_path,
                )
                ] = idx
            ordered: dict[int, RenderResult] = {}
            nvenc_oom_error: RuntimeError | None = None
            for future in as_completed(futures):
                try:
                    idx, result = future.result()
                    ordered[idx] = result
                    release_gpu_memory()  # Clean after each parallel job finishes
                except RuntimeError as exc:
                    if self._is_nvenc_oom_error(exc):
                        nvenc_oom_error = exc
                        LOGGER.warning(
                            "NVENC OOM in parallel render. Falling back to sequential for remaining clips."
                    )
                        continue
                    raise
        if nvenc_oom_error is not None:
            for idx, clip in enumerate(clips, start=1):
                if idx in ordered:
                    results.append(ordered[idx])
                    continue
                results.append(
                    self.render_single_clip(
                        source_video=source_video,
                        transcript=transcript,
                        clip=clip,
                        output_dir=out_dir,
                        slug=slug,
                        index=idx,
                        transcript_json_path=transcript_json_path,
                )
            )
            return results
        for idx in sorted(ordered):
            results.append(ordered[idx])
        return results

    def _render_single_isolated(
        self,
        source_video: Path,
        transcript: Transcript,
        clip: ClipCandidate,
        output_dir: Path,
        slug: str,
        index: int,
        transcript_json_path: str | Path | None = None,
    ) -> tuple[int, RenderResult]:
        isolated = ShortsRenderer(self.config)
        result = isolated.render_single_clip(
            source_video=source_video,
            transcript=transcript,
            clip=clip,
            output_dir=output_dir,
            slug=slug,
            index=index,
            transcript_json_path=transcript_json_path,
        )
        return index, result

    def render_single_clip(
        self,
        source_video: str | Path,
        transcript: Transcript,
        clip: ClipCandidate,
        output_dir: str | Path | None = None,
        slug: str = "video",
        index: int = 1,
        transcript_json_path: str | Path | None = None,
        subtitle_override_path: str | Path | None = None,
        tracking_override: TrackingPath | None = None,
        output_path_override: str | Path | None = None,
    ) -> RenderResult:
        source_video = Path(source_video).resolve()
        out_dir = Path(output_dir).resolve() if output_dir else self.config.clips_dir
        if clip.source_spans:
            if subtitle_override_path is not None or tracking_override is not None:
                raise ValueError('Montage overrides must target the assembled source via its render metadata')
            duration = self._probe_media_duration(source_video)
            if any(p.end>duration+.02 for p in clip.source_spans):
                raise ValueError('Montage references media beyond the source ending')
            from .montage import prepare_montage
            edited, mapped, local_clip, mapped_path = prepare_montage(source_video,transcript,clip,out_dir,self.config)
            result = self.render_single_clip(edited,mapped,local_clip,out_dir,slug,index,
                transcript_json_path=mapped_path,output_path_override=output_path_override)
            result.clip = clip
            return result
        ensure_dir(out_dir)
        ensure_dir(self.config.temp_dir)
        ensure_dir(self.config.subtitles_dir)

        video_duration = self._probe_media_duration(source_video)
        clip = clip.clamp(video_duration if video_duration > 0 else clip.end)
        if clip.end <= clip.start:
            raise ValueError("Invalid clip interval")

        if output_path_override is not None:
            output_path = Path(output_path_override).expanduser().resolve()
            clip_id = output_path.stem
            out_dir = output_path.parent
            ensure_dir(out_dir)
        else:
            clip_id = f"{slug}_{self.config.output_basename}_{index:02d}"
            output_path = out_dir / f"{clip_id}.{self.config.output_video_extension}"
        from .utils import preserve_existing
        preserve_existing(output_path)
        # Sidecars belong to the same generation as the final render.
        subtitle_dir = ensure_dir(self.config.subtitles_dir / slug / out_dir.name / clip_id)
        generated_subtitle_path = subtitle_dir / f"{clip_id}.ass"
        tracking_store_path = subtitle_dir / f"{clip_id}.tracking.json"
        render_meta_path = subtitle_dir / f"{clip_id}.meta.json"
        crop_meta_path = subtitle_dir / f"{clip_id}.crop.json"
        subtitle_path: Optional[Path] = None
        subtitle_overlay_path: Optional[Path] = None

        if subtitle_override_path is not None:
            override_path = Path(subtitle_override_path).expanduser()
            if not override_path.is_absolute():
                override_path = (Path.cwd() / override_path).resolve()
            else:
                override_path = override_path.resolve()
            if not override_path.exists():
                raise FileNotFoundError(
                    f"Subtitle override file not found: {override_path}"
            )
            subtitle_path = override_path
        elif self.config.captions_enabled:
            self._new_caption_builder().build(
                transcript=transcript,
                output_path=generated_subtitle_path,
                clip_start=clip.start,
                clip_end=clip.end,
        )
            subtitle_path = generated_subtitle_path

        if tracking_override is None:
            try:
                if not self.config.enable_face_tracking:
                    raise RuntimeError("Face tracking disabled")
                path = self._new_tracker().track_clip(
                    video_path=source_video,
                    start_s=clip.start,
                    end_s=clip.end,
                    target_w=self.config.output_width,
                    target_h=self.config.output_height,

            )
            except Exception as exc:
                LOGGER.warning(
                    "Tracker failed for %s; preserving the full source shot: %s",
                    source_video,
                    exc,
            )
                path = self._build_center_tracking_path(
                    video_path=source_video,
                    start_s=clip.start,
                    end_s=clip.end,
            )
        else:
            path = tracking_override
        write_json(crop_meta_path, path.to_dict())
        write_json(tracking_store_path, path.to_dict())

        should_zoom = (
            self.config.cinematic_zoom
            and self._normalize_layout_mode(clip.layout_mode) not in ("duo_split", "reaction_split")
        )

        zoom_applied = False
        raw_vertical_path = output_path.with_name(f"{output_path.stem}_raw.{self.config.output_video_extension}")
        zoomed_path = output_path.with_name(f"{output_path.stem}_zoomed.{self.config.output_video_extension}")

        subtitle_path_used_in_render = self._run_ffmpeg_native_vertical(
            source_video=source_video,
            clip=clip,
            output_path=raw_vertical_path,
            subtitle_path=subtitle_path,
            tracking=path,
            transcript=transcript,
            burn_subtitles=False,
        )
        stored_subtitle_path = subtitle_path_used_in_render or subtitle_path
        
        # Sync remapped subtitles back to the final subtitle directory
        if (
            subtitle_path_used_in_render is not None 
            and subtitle_path_used_in_render != subtitle_path
        ):
            # This means a .jump.ass was created
            ensure_parent_dir(generated_subtitle_path)
            shutil.copy2(subtitle_path_used_in_render, generated_subtitle_path)
            # Also sync the .srt version if it exists
            srt_remapped = Path(subtitle_path_used_in_render).with_suffix(".srt")
            if srt_remapped.exists():
                shutil.copy2(srt_remapped, Path(generated_subtitle_path).with_suffix(".srt"))
            stored_subtitle_path = generated_subtitle_path
        elif subtitle_path is not None and subtitle_path != generated_subtitle_path:
            # Legacy/Override sync logic: Only copy if it is a valid subtitle text file
            if subtitle_path.suffix.lower() in [".ass", ".srt"]:
                ensure_parent_dir(generated_subtitle_path)
                shutil.copy2(subtitle_path, generated_subtitle_path)
                stored_subtitle_path = generated_subtitle_path

        if (
            self.config.captions_export_overlay_video
            and stored_subtitle_path is not None
        ):
            subtitle_overlay_path = subtitle_dir / (
                f"{clip_id}.overlay.{self.config.captions_overlay_video_ext}"
            )
            duration_s = self._probe_media_duration(raw_vertical_path)
            self._render_subtitle_overlay_video(
                subtitle_path=stored_subtitle_path,
                output_path=subtitle_overlay_path,
                duration_s=duration_s,
            )

        if should_zoom:
            LOGGER.info("Aplicando zoom cinemático Face-Lock ANTES da queima de legendas...")
            try:
                from .post_processor import apply_cinematic_zoom
                apply_cinematic_zoom(
                    input_video=raw_vertical_path,
                    output_video=zoomed_path,
                    tracking_points=path.points if path else None,
                    eye_line_ratio=self.config.tracking_eye_line_ratio,
                )
                zoom_applied = True
            except Exception as exc:
                LOGGER.error("Falha ao aplicar zoom cinemático em %s: %s", raw_vertical_path.name, exc)

        base_for_burn = zoomed_path if (zoom_applied and zoomed_path.exists()) else raw_vertical_path

        # Profile 3: Extraindo arquivos modulares (vídeo limpo com zoom, SEM legendas!)
        LOGGER.info("Profile 3: Extraindo arquivos modulares limpos em subpasta modular/...")
        modular_dir = output_path.parent / "modular"
        modular_dir.mkdir(parents=True, exist_ok=True)
        voz_path = modular_dir / f"{output_path.stem}_voz.wav"
        video_only_path = modular_dir / f"{output_path.stem}_video.{self.config.output_video_extension}"
        split_cmd = (
            ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        )
        split_cmd.extend([
            "-y", "-threads", "4", "-v", "warning",
            "-i", str(base_for_burn),
            "-map", "0:v", "-c:v", "copy", "-an", str(video_only_path),
            "-map", "0:a", "-c:a", "pcm_s16le", "-vn", str(voz_path),
        ])
        run_cmd(split_cmd, check=True)
        LOGGER.info(
            "Arquivos modulares salvos em %s: %s, %s",
            modular_dir.name,
            video_only_path.name,
            voz_path.name,
        )

        # Queima as legendas NO TOPO do vídeo se configurado
        if (
            self.config.captions_burn_in_video
            and stored_subtitle_path
            and stored_subtitle_path.exists()
        ):
            LOGGER.info("Queimando legendas no topo do vídeo: %s", stored_subtitle_path.name)
            sub_escaped = str(stored_subtitle_path).replace("\\", "\\\\").replace(":", r"\:")
            codec = "dnxhd" if self.config.davinci_resolve_compatibility else self.config.ffmpeg_video_codec
            is_nvenc = "nvenc" in codec.lower()
            burn_cmd = (
                ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
            )
            burn_cmd.extend([
                "-y", "-threads", "4", "-v", "warning",
                "-i", str(base_for_burn),
                "-vf", f"subtitles='{sub_escaped}'",
                "-c:v", codec,
                *(["-profile:v", "dnxhr_hq"] if self.config.davinci_resolve_compatibility else ["-preset", self.config.ffmpeg_nvenc_preset]),
                "-b:v", self.config.ffmpeg_video_bitrate,
                "-maxrate", self.config.ffmpeg_video_bitrate,
                "-bufsize", "20M",
                "-pix_fmt", "yuv422p" if self.config.davinci_resolve_compatibility else "yuv420p",
                "-c:a", "copy",
                str(output_path),
            ])
            try:
                run_cmd(burn_cmd, check=True)
            except RuntimeError as exc:
                if is_nvenc and ("nvenc" in str(exc).lower() or "encoder" in str(exc).lower()):
                    LOGGER.warning("NVENC burn-in falhou, tentando via CPU (libx264): %s", exc)
                    burn_cmd_cpu = (
                        ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
                    )
                    burn_cmd_cpu.extend([
                        "-y", "-threads", "4", "-v", "warning",
                        "-i", str(base_for_burn),
                        "-vf", f"subtitles='{sub_escaped}'",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        "-pix_fmt", "yuv420p",
                        "-c:a", "copy",
                        str(output_path),
                    ])
                    run_cmd(burn_cmd_cpu, check=True)
                else:
                    raise
        else:
            LOGGER.info("Exportando vídeo limpo sem queima de legendas: %s", output_path.name)
            shutil.copy2(base_for_burn, output_path)

        # Limpeza dos arquivos temporários raw e zoomed
        if raw_vertical_path.exists() and raw_vertical_path != output_path:
            raw_vertical_path.unlink(missing_ok=True)
        if zoomed_path.exists() and zoomed_path != output_path:
            zoomed_path.unlink(missing_ok=True)

        transcript_json_resolved = (
            Path(transcript_json_path).expanduser().resolve()
            if transcript_json_path is not None
            else None
        )
        self._write_render_metadata(
            meta_path=render_meta_path,
            clip_id=clip_id,
            clip=clip,
            slug=slug,
            index=index,
            output_path=output_path,
            source_video=source_video,
            transcript_json_path=transcript_json_resolved,
            subtitle_path=stored_subtitle_path,
            subtitle_overlay_path=subtitle_overlay_path,
            tracking_path=tracking_store_path,
        )

        LOGGER.info("Rendered clip: %s", output_path)
        release_gpu_memory()
        return RenderResult(
            clip=clip,
            output_path=output_path,
            subtitle_path=stored_subtitle_path,
            subtitle_overlay_path=subtitle_overlay_path,
            crop_meta_path=crop_meta_path,
            tracking_data=path,
            zoom_applied=zoom_applied,
        )

    def _run_ffmpeg_native_vertical(
        self,
        source_video: Path,
        clip: ClipCandidate,
        output_path: Path,
        subtitle_path: Optional[Path],
        tracking: TrackingPath,
        transcript: Transcript,
        burn_subtitles: bool = True,
    ) -> Optional[Path]:
        ensure_parent_dir(output_path)

        max_x = max(0, tracking.source_width - tracking.crop_width)
        max_y = max(0, tracking.source_height - tracking.crop_height)
        half_h = max(2, self.config.output_height // 2)
        if tracking.duo_left_points and getattr(tracking.duo_left_points[0], "h", None):
            duo_crop_height = int(tracking.duo_left_points[0].h)
            duo_crop_width = int(tracking.duo_left_points[0].w)
        else:
            duo_crop_height = max(2, min(tracking.source_height, int(round(tracking.source_height * 0.58))))
            duo_crop_width = max(
                2,
                min(
                    tracking.source_width,
                    int(
                        round(
                            duo_crop_height
                            * (float(self.config.output_width) / float(half_h))
                        )
                    ),
                ),
            )
            if duo_crop_width % 2:
                duo_crop_width += 1
            if duo_crop_height % 2:
                duo_crop_height += 1
        duo_crop_width = min(
            duo_crop_width, tracking.source_width - (tracking.source_width % 2)
        )
        duo_max_x = max(0, tracking.source_width - duo_crop_width)
        duo_max_y = max(0, tracking.source_height - duo_crop_height)
        layout_mode = self._normalize_layout_mode(clip.layout_mode)
        if self.config.force_single_layout:
            layout_mode = "single"
        duo_left_track = (
            tracking.duo_left_points if tracking.duo_left_points else tracking.points
        )
        duo_right_track = (
            tracking.duo_right_points
            if tracking.duo_right_points
            else tracking.secondary_points
        )
        b_roll_path = self._resolve_asset_path(
            clip.b_roll_overlay or self.config.background_video_path
            if layout_mode == "reaction_split"
            else clip.b_roll_overlay
        )
        duo_split_windows: list[tuple[float, float]] = []
        if (
            layout_mode == "single"
            and not self.config.force_single_layout
            and b_roll_path is None
            and len(duo_right_track) >= 2
        ):
            duo_split_windows = self._coerce_windows(
                windows=tracking.active_speaker_multi_face_windows,
                clip_duration=clip.duration,
                max_windows=24,
                max_window_seconds=2.4,
        )
            if duo_split_windows:
                total_duo = sum(end - start for start, end in duo_split_windows)
                LOGGER.info(
                    "Dynamic duo-split windows detected: %d (%.2fs / %.2fs)",
                    len(duo_split_windows),
                    total_duo,
                    clip.duration,
            )
        single_speaker_windows: list[tuple[float, float]] = []
        if layout_mode == "single" and tracking.fit_scene_mode and b_roll_path is None:
            single_speaker_windows = self._coerce_windows(
                windows=tracking.active_speaker_single_windows,
                clip_duration=clip.duration,
                max_windows=10000,
                max_window_seconds=0.0,
        )
            if duo_split_windows:
                single_speaker_windows = self._subtract_windows(
                    base_windows=single_speaker_windows,
                    minus_windows=duo_split_windows,
                    gap=0.08,
            )
            if single_speaker_windows:
                total_single = sum(end - start for start, end in single_speaker_windows)
                LOGGER.info(
                    "Dynamic single windows detected: %d (%.2fs / %.2fs)",
                    len(single_speaker_windows),
                    total_single,
                    clip.duration,
            )
        disable_zoom_for_multiface = (
            tracking.active_speaker_multi_face_ratio
            >= self.config.multiface_disable_zoom_threshold
            and not duo_split_windows
        )
        top_banner_path = self._resolve_asset_path(self.config.top_banner_path)
        bottom_logo_path = self._resolve_asset_path(self.config.bottom_logo_path)
        contextual_overlays = self._resolve_contextual_overlays(
            overlays=clip.contextual_overlays,
            clip=clip,
        )

        extra_inputs: list[str] = []
        input_labels: dict[str, str] = {}
        input_index = 1
        if b_roll_path is not None:
            extra_inputs.extend(["-stream_loop", "-1", "-i", str(b_roll_path)])
            input_labels["broll"] = f"{input_index}:v"
            input_index += 1
        audio_overlays: dict[str, str] = {}
        for idx, overlay in enumerate(contextual_overlays):
            is_audio = (
                str(overlay.path)
                .lower()
                .endswith((".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a"))
        )
            if is_audio:
                extra_inputs.extend(["-i", str(overlay.path)])
                audio_overlays[f"context_{idx}"] = f"{input_index}:a"
            else:
                extra_inputs.extend(["-stream_loop", "-1", "-i", str(overlay.path)])
                input_labels[f"context_{idx}"] = f"{input_index}:v"
            input_index += 1
        if top_banner_path is not None:
            extra_inputs.extend(["-stream_loop", "-1", "-i", str(top_banner_path)])
            input_labels["top_banner"] = f"{input_index}:v"
            input_index += 1
        if bottom_logo_path is not None:
            extra_inputs.extend(["-stream_loop", "-1", "-i", str(bottom_logo_path)])
            input_labels["bottom_logo"] = f"{input_index}:v"
            input_index += 1

        compressed_points = self._compress_points(tracking.points)
        compressed_points_secondary = self._compress_points(tracking.secondary_points)
        compressed_duo_points = self._compress_points(
            self._retarget_points(
                points=duo_left_track,
                from_crop_width=tracking.crop_width,
                to_crop_width=duo_crop_width,
                max_x=duo_max_x,
        ),
            min_time_delta=0.12,
            min_coord_delta=2.0,
        )
        compressed_duo_points_secondary = self._compress_points(
            self._retarget_points(
                points=duo_right_track,
                from_crop_width=tracking.crop_width,
                to_crop_width=duo_crop_width,
                max_x=duo_max_x,
        ),
            min_time_delta=0.12,
            min_coord_delta=2.0,
        )
        if len(compressed_points) < len(tracking.points):
            LOGGER.debug(
                "Compressed tracking points: %s -> %s",
                len(tracking.points),
                len(compressed_points),
        )
        jump_keep_windows: list[tuple[float, float]] = []
        if self.config.silence_jump_cuts_enabled:
            jump_keep_windows = self._build_jump_keep_windows(
                source_video=source_video,
                clip=clip,
                transcript=transcript,
            )
            if jump_keep_windows:
                removed = max(
                    0.0,
                    clip.duration
                    - sum(end - start for start, end in jump_keep_windows),
                )
                LOGGER.info(
                    "Jump cuts enabled: kept %d speech windows (removed %.2fs silence)",
                    len(jump_keep_windows),
                    removed,
                )
        subtitle_for_render = subtitle_path
        if subtitle_path is not None and subtitle_path.exists() and jump_keep_windows:
            remapped_subtitle_path = (
                self.config.temp_dir / f"{output_path.stem}.jump.ass"
            )
            remapped_events = self._remap_subtitle_to_jump_cuts(
                source_subtitle_path=subtitle_path,
                target_subtitle_path=remapped_subtitle_path,
                keep_windows=jump_keep_windows,
            )
            if remapped_subtitle_path.exists():
                subtitle_for_render = remapped_subtitle_path
                LOGGER.info(
                    "Remapped %d subtitle events for jump cuts.", remapped_events
                )
        if jump_keep_windows:
            jump_output_duration = max(
                0.001, sum(end - start for start, end in jump_keep_windows)
            )
            remapped_primary_points = self._remap_points_to_jump_cuts(
                points=compressed_points,
                keep_windows=jump_keep_windows,
            )
            remapped_secondary_points = self._remap_points_to_jump_cuts(
                points=compressed_points_secondary,
                keep_windows=jump_keep_windows,
        )
            remapped_duo_points = self._remap_points_to_jump_cuts(
                points=compressed_duo_points,
                keep_windows=jump_keep_windows,
        )
            remapped_duo_points_secondary = self._remap_points_to_jump_cuts(
                points=compressed_duo_points_secondary,
                keep_windows=jump_keep_windows,
        )
            if remapped_primary_points:
                compressed_points = remapped_primary_points
            if remapped_secondary_points:
                compressed_points_secondary = remapped_secondary_points
            if remapped_duo_points:
                compressed_duo_points = remapped_duo_points
            if remapped_duo_points_secondary:
                compressed_duo_points_secondary = remapped_duo_points_secondary
            duo_split_windows = self._coerce_windows(
                windows=self._remap_windows_to_jump_cuts(
                    windows=duo_split_windows,
                    keep_windows=jump_keep_windows,
            ),
                clip_duration=jump_output_duration,
                max_windows=24,
                max_window_seconds=2.4,
        )
            single_speaker_windows = self._coerce_windows(
                windows=self._remap_windows_to_jump_cuts(
                    windows=single_speaker_windows,
                    keep_windows=jump_keep_windows,
            ),
                clip_duration=jump_output_duration,
                max_windows=10000,
                max_window_seconds=0.0,
        )
            if duo_split_windows and single_speaker_windows:
                single_speaker_windows = self._subtract_windows(
                    base_windows=single_speaker_windows,
                    minus_windows=duo_split_windows,
                    gap=0.08,
            )

        point_budgets = (60, 40, 20)
        last_exc: RuntimeError | None = None
        release_gpu_memory()
        encode_profiles = self._build_encode_profiles()
        for budget in point_budgets:
            sample_points = self._downsample_points(
                compressed_points, max_points=budget
        )
            sample_points_secondary = self._downsample_points(
                compressed_points_secondary,
                max_points=budget,
        )
            duo_points_source = self._points_for_windows(
                points=compressed_duo_points,
                windows=duo_split_windows,
        )
            duo_points_secondary_source = self._points_for_windows(
                points=compressed_duo_points_secondary,
                windows=duo_split_windows,
        )
            duo_budget = max(16, min(96, budget * 3))
            duo_points = self._downsample_points(
                duo_points_source,
                max_points=duo_budget,
        )
            duo_points_secondary = self._downsample_points(
                duo_points_secondary_source,
                max_points=duo_budget,
        )
            x_expr = self._build_coord_expr(sample_points, axis="x", max_value=max_x)
            y_expr = self._build_coord_expr(sample_points, axis="y", max_value=max_y)
            x_expr_secondary = self._build_coord_expr(
                sample_points_secondary,
                axis="x",
                max_value=max_x,
        )
            y_expr_secondary = self._build_coord_expr(
                sample_points_secondary,
                axis="y",
                max_value=max_y,
        )
            x_expr_duo = self._build_coord_expr(
                duo_points, axis="x", max_value=duo_max_x
        )
            y_expr_duo = self._build_coord_expr(
                duo_points, axis="y", max_value=duo_max_y
        )
            x_expr_secondary_duo = self._build_coord_expr(
                duo_points_secondary,
                axis="x",
                max_value=duo_max_x,
        )
            y_expr_secondary_duo = self._build_coord_expr(
                duo_points_secondary,
                axis="y",
                max_value=duo_max_y,
        )
            filter_complex, video_label, audio_label = self._build_filter_complex(
                tracking=tracking,
                x_expr=x_expr,
                y_expr=y_expr,
                x_expr_secondary=x_expr_secondary,
                y_expr_secondary=y_expr_secondary,
                x_expr_duo=x_expr_duo,
                y_expr_duo=y_expr_duo,
                x_expr_secondary_duo=x_expr_secondary_duo,
                y_expr_secondary_duo=y_expr_secondary_duo,
                clip=clip,
                transcript=transcript,
                subtitle_path=subtitle_for_render if burn_subtitles else None,
                max_x=max_x,
                duo_crop_width=duo_crop_width,
                duo_crop_height=duo_crop_height,
                duo_max_x=duo_max_x,
                layout_mode=layout_mode,
                input_labels=input_labels,
                contextual_overlays=contextual_overlays,
                disable_zoom_for_multiface=disable_zoom_for_multiface,
                duo_split_windows=duo_split_windows,
                single_speaker_windows=single_speaker_windows,
                jump_keep_windows=jump_keep_windows,
        )
            audio_map_label = f"[{audio_label}]" if audio_label else "[0:a]"

            if audio_overlays:
                mix_inputs = audio_map_label
                amix_count = 1
                for idx, overlay in enumerate(contextual_overlays):
                    a_label = audio_overlays.get(f"context_{idx}")
                    if a_label:
                        delay_ms = int(overlay.start_rel * 1000)
                        delayed_label = f"[sfx{idx}]"
                        filter_complex += f";[{a_label}]adelay={delay_ms}|{delay_ms}[delayed{idx}];[delayed{idx}]volume=0.8{delayed_label}"
                        mix_inputs += delayed_label
                        amix_count += 1

                mixed_label = "[amixed]"
                filter_complex += f";{mix_inputs}amix=inputs={amix_count}:duration=first:dropout_transition=2{mixed_label}"
                audio_map_label = mixed_label

            if not audio_label and not audio_overlays:
                audio_map_label = "0:a?"

            # Apply broadcast-standard EBU R128 loudness normalization (-14 LUFS)
            if getattr(self.config, "audio_normalization_enabled", True):
                target_lufs = float(getattr(self.config, "audio_target_lufs", -14.0))
                norm_in = "[0:a]" if audio_map_label == "0:a?" else audio_map_label
                norm_label = "[anorm]"
                filter_complex += f";{norm_in}loudnorm=I={target_lufs:.1f}:LRA=7:TP=-1.5{norm_label}"
                audio_map_label = norm_label
            parse_retry = False
            # -shortest is only needed when there are extra looped inputs (b-roll, banners)
            # that would otherwise run forever. Without it, -t is enough to end the encode.
            output_duration = max(0.001, clip.end - clip.start)
            if jump_keep_windows:
                output_duration = max(
                    0.001,
                    sum(end - start for start, end in jump_keep_windows),
            )
            # IMPORTANT: keep -ss / -t AFTER -i when using CUDA hwaccel decode.
            # With -ss/-t BEFORE -i, cuvid can stop feeding frames early while audio
            # continues, producing a short video stream (~10–20s) on long clips.
            for profile_idx, profile in enumerate(encode_profiles):
                cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
                cmd.extend(
                [
                    "-y",
                    "-threads",
                    "4",
                    "-filter_complex_threads",
                    "2",
                    "-v",
                    "warning",
                    ]
            )
                if not self._profile3_cpu_decode():
                    cmd.extend(
                    [
                            "-hwaccel",
                            profile["hwaccel"],
                            "-hwaccel_output_format",
                            "cuda",
                        ]
                )
                if self._profile3_cpu_decode():
                    cmd.extend(
                    [
                            "-ss",
                            f"{clip.start:.3f}",
                            "-t",
                            f"{clip.duration:.3f}",
                        "-i",
                            str(source_video),
                        ]
                )
                else:
                    cmd.extend(
                    [
                        "-i",
                            str(source_video),
                            "-ss",
                            f"{clip.start:.3f}",
                            "-t",
                            f"{output_duration:.3f}",
                        ]
                )
                cmd.extend(
                [
                        *extra_inputs,
                        "-filter_complex",
                        filter_complex,
                    "-map",
                        f"[{video_label}]",
                    "-map",
                        audio_map_label,
                        "-r",
                        str(self.config.output_fps),
                    "-c:v",
                        "dnxhd" if self.config.davinci_resolve_compatibility else self.config.ffmpeg_video_codec,
                        *( ["-profile:v", "dnxhr_hq"] if self.config.davinci_resolve_compatibility else ["-preset", self.config.ffmpeg_nvenc_preset, *profile["nvenc_extra"]] ),
                        "-b:v",
                        self.config.ffmpeg_video_bitrate,
                        "-maxrate",
                        self.config.ffmpeg_video_bitrate,
                        "-bufsize",
                        "20M",
                        "-pix_fmt",
                        "yuv422p" if self.config.davinci_resolve_compatibility else "yuv420p",
                        "-c:a",
                        "pcm_s16le" if self.config.davinci_resolve_compatibility else "aac",
                        *( [] if self.config.davinci_resolve_compatibility else ["-b:a", "192k"] ),
                    ]
            )
                # Enforce exact output duration to prevent short inputs (like SFX)
                # from prematurely ending the video via -shortest, or long overlays
                # from unintentionally extending the final output.
                cmd.extend(["-t", f"{output_duration:.3f}"])
                cmd.append(str(output_path))
                try:
                    run_cmd(cmd, check=True)
                    self._warn_muxed_av_duration_mismatch(
                        output_path=output_path,
                        expected_s=output_duration,
                )
                    return subtitle_for_render
                except RuntimeError as exc:
                    if (
                        "Missing ')' or too many args" in str(exc)
                        and budget != point_budgets[-1]
                ):
                        last_exc = exc
                        parse_retry = True
                        LOGGER.warning(
                            "Retrying ffmpeg crop with fewer points (%s -> %s)",
                            budget,
                            point_budgets[point_budgets.index(budget) + 1],
                    )
                        break
                    if self._is_nvenc_oom_error(exc) and profile_idx < (
                        len(encode_profiles) - 1
                ):
                        last_exc = exc
                        LOGGER.warning(
                            "NVENC OOM detected. Retrying clip with low-memory encode profile."
                    )
                        continue
                    raise
            if parse_retry:
                continue

        if last_exc is not None:
            raise last_exc
        return subtitle_for_render

    @staticmethod
    def _downsample_points(points, max_points):
        if len(points) <= max_points:
            return list(points)
        keep = {0, len(points)-1}
        for i,p in enumerate(points):
            if getattr(p, "cut", False):
                keep.update((max(0,i-1), i))
        step = max(1, len(points)//max_points)
        keep.update(range(0, len(points), step))
        return [points[i] for i in sorted(keep)]

    @staticmethod
    def _compress_points(
        points,
        min_time_delta: float = 0.25,
        min_coord_delta: float = 6.0,
    ) -> list:
        if len(points) <= 2:
            return list(points)
        reduced = [points[0]]
        for i, point in enumerate(points[1:-1], start=1):
            if getattr(point, "cut", False) or getattr(points[i+1], "cut", False):
                reduced.append(point)
                continue
            prev = reduced[-1]
            if (
                (point.t - prev.t) < min_time_delta
                and abs(point.x - prev.x) < min_coord_delta
                and abs(point.y - prev.y) < min_coord_delta
        ):
                continue
            reduced.append(point)
        if reduced[-1] != points[-1]:
            reduced.append(points[-1])
        return reduced

    @staticmethod
    def _points_for_windows(
        points,
        windows: list[tuple[float, float]],
        pad_seconds: float = 0.22,
    ) -> list:
        if not points:
            return []
        if not windows:
            return list(points)
        selected = []
        for point in points:
            for start, end in windows:
                if (start - pad_seconds) <= point.t <= (end + pad_seconds):
                    selected.append(point)
                    break
        if len(selected) >= 2:
            return selected
        return list(points)

    @staticmethod
    def _retarget_points(
        points,
        from_crop_width: int,
        to_crop_width: int,
        max_x: int,
    ) -> list:
        if not points:
            return []
        out = []
        for point in points:
            pw = getattr(point, "w", None)
            cur_w = float(pw) if pw is not None and pw > 0 else float(from_crop_width)
            shift = (cur_w - float(to_crop_width)) * 0.5
            x = float(point.x) + shift
            x = max(0.0, min(float(max_x), x))
            out.append(type(point)(t=point.t, x=x, y=point.y, w=to_crop_width, h=getattr(point, "h", None)))
        return out

    def _build_filter_complex(
        self,
        tracking: TrackingPath,
        x_expr: str,
        y_expr: str,
        x_expr_secondary: str,
        y_expr_secondary: str,
        x_expr_duo: str,
        y_expr_duo: str,
        x_expr_secondary_duo: str,
        y_expr_secondary_duo: str,
        clip: ClipCandidate,
        transcript: Transcript,
        subtitle_path: Optional[Path],
        max_x: int,
        duo_crop_width: int,
        duo_crop_height: int,
        duo_max_x: int,
        layout_mode: str,
        input_labels: dict[str, str],
        contextual_overlays: list[ResolvedContextualOverlay],
        disable_zoom_for_multiface: bool,
        duo_split_windows: list[tuple[float, float]],
        single_speaker_windows: list[tuple[float, float]],
        jump_keep_windows: list[tuple[float, float]],
    ) -> tuple[str, str, Optional[str]]:
        filters: list[str] = []
        audio_label: Optional[str] = None
        source_video_label = "0:v"
        if jump_keep_windows:
            source_video_label, audio_label = self._append_jump_cut_filters(
                filters=filters,
                keep_windows=jump_keep_windows,
                fps=self.config.output_fps,
        )
        layout_mode = self._normalize_layout_mode(layout_mode)
        dynamic_duo_enabled = layout_mode == "single" and bool(duo_split_windows)
        dynamic_single_enabled = (
            layout_mode == "single"
            and tracking.fit_scene_mode
            and bool(single_speaker_windows)
        )
        fit_source_label = source_video_label
        duo_source_label = source_video_label
        if source_video_label != "0:v" and (
            dynamic_single_enabled or dynamic_duo_enabled
        ):
            if dynamic_single_enabled and dynamic_duo_enabled:
                filters.append(
                    f"[{source_video_label}]split=3[jcsrc_main][jcsrc_fit][jcsrc_duo]"
            )
                source_video_label = "jcsrc_main"
                fit_source_label = "jcsrc_fit"
                duo_source_label = "jcsrc_duo"
            else:
                filters.append(f"[{source_video_label}]split=2[jcsrc_main][jcsrc_alt]")
                source_video_label = "jcsrc_main"
                fit_source_label = "jcsrc_alt"
                duo_source_label = "jcsrc_alt"
        current_label = source_video_label
        label_index = 0
        clip_duration = max(0.001, clip.duration)
        if layout_mode == "reaction_split" and "broll" in input_labels:
            top_src = "lay_top_src"
            top_track = "lay_top_track"
            bottom_src = "lay_bottom_src"
            base_out = "v0"
            half_h = max(2, self.config.output_height // 2)
            if tracking.fit_scene_mode:
                filters.append(
                    f"[{source_video_label}]{self._fit_scene_filter(self.config.output_width, half_h)}[{top_src}]"
            )
            else:
                filters.append(
                    f"[{source_video_label}]{self._crop_scale_cuda_chain(tracking.crop_width, tracking.crop_height, x_expr, y_expr, self.config.output_width, half_h)}[{top_src}]"
            )
            filters.append(
                f"[{top_src}]hwupload,scale_cuda={self.config.output_width}:{half_h}:interp_algo=4,hwdownload,format=nv12,setsar=1[{top_track}]"
        )
            filters.append(
                f"[{input_labels['broll']}]scale={self.config.output_width}:{half_h}:force_original_aspect_ratio=decrease,"
                f"pad={self.config.output_width}:{half_h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
                f"[{bottom_src}]"
        )
            filters.append(f"[{top_track}][{bottom_src}]vstack=inputs=2[{base_out}]")
            current_label = base_out
            label_index = 1
        elif layout_mode == "duo_split":
            current_label, label_index = self._append_duo_layout(
                filters=filters,
                label_index=label_index,
                source_label=duo_source_label,
                tracking=tracking,
                x_expr=x_expr_duo,
                y_expr=y_expr_duo,
                x_expr_secondary=x_expr_secondary_duo,
                y_expr_secondary=y_expr_secondary_duo,
                max_x=duo_max_x,
                duo_crop_width=duo_crop_width,
                duo_crop_height=duo_crop_height,
        )
        else:
            if tracking.fit_scene_mode and not dynamic_single_enabled:
                crop_filter = self._fit_scene_filter(
                    self.config.output_width,
                    self.config.output_height,
            )
            else:
                crop_filter = self._crop_scale_cuda_chain(
                    tracking.crop_width,
                    tracking.crop_height,
                    x_expr,
                    y_expr,
                    self.config.output_width,
                    self.config.output_height,
            )
            current_label, label_index = self._append_filter(
                filters, current_label, label_index, crop_filter
        )
            if dynamic_single_enabled:
                fit_label, label_index = self._append_filter(
                    filters,
                    fit_source_label,
                    label_index,
                    self._fit_scene_filter(
                        self.config.output_width,
                        self.config.output_height,
                ),
            )
                enable_expr = self._build_enable_expr(single_speaker_windows)
                if enable_expr:
                    current_label, label_index = self._append_overlay_filter(
                        filters=filters,
                        base_label=fit_label,
                        overlay_label=current_label,
                        label_index=label_index,
                        x_expr="0",
                        y_expr="0",
                        enable_expr=enable_expr,
                )
                else:
                    current_label = fit_label

            if dynamic_duo_enabled:
                duo_label, label_index = self._append_duo_layout(
                    filters=filters,
                    label_index=label_index,
                    source_label=duo_source_label,
                    tracking=tracking,
                    x_expr=x_expr_duo,
                    y_expr=y_expr_duo,
                    x_expr_secondary=x_expr_secondary_duo,
                    y_expr_secondary=y_expr_secondary_duo,
                    max_x=duo_max_x,
                    duo_crop_width=duo_crop_width,
                    duo_crop_height=duo_crop_height,
            )
                enable_expr = self._build_enable_expr(duo_split_windows)
                if enable_expr:
                    current_label, label_index = self._append_overlay_filter(
                        filters=filters,
                        base_label=current_label,
                        overlay_label=duo_label,
                        label_index=label_index,
                        x_expr="0",
                        y_expr="0",
                        enable_expr=enable_expr,
            )

        for idx, overlay in enumerate(contextual_overlays):
            overlay_label = input_labels.get(f"context_{idx}")
            if not overlay_label:
                continue
            scaled_label = f"ctxs{label_index}"
            filters.append(
                f"[{overlay_label}]scale='min({overlay.max_width},iw*{overlay.scale:.3f})':-2[{scaled_label}]"
        )
            chest_y = min(
                self.config.output_height - 220,
                int(
                    round(
                        self.config.output_height * self.config.tracking_eye_line_ratio
                )
            )
                + max(0, int(overlay.y_offset)),
        )
            current_label, label_index = self._append_overlay_filter(
                filters=filters,
                base_label=current_label,
                overlay_label=scaled_label,
                label_index=label_index + 1,
                x_expr="(W-w)/2",
                y_expr=str(chest_y),
                enable_expr=f"between(t,{overlay.start_rel:.3f},{overlay.end_rel:.3f})",
        )

        if (
            self.config.captions_burn_in_video
            and subtitle_path
            and subtitle_path.exists()
        ):
            # Log the beginning of the subtitle file for debugging purposes
            try:
                raw_sub = subtitle_path.read_text(encoding="utf-8")
                head_lines = "\n".join(raw_sub.splitlines()[:20])
                LOGGER.info(
                    "Burn-in subtitles (%s) head:\n%s", subtitle_path, head_lines
            )
            except Exception:
                LOGGER.exception(
                    "Failed to read subtitle file for logging: %s", subtitle_path
            )

            sub_escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", r"\:")
            current_label, label_index = self._append_filter(
                filters,
                current_label,
                label_index,
                f"subtitles='{sub_escaped}'",
        )

        if "top_banner" in input_labels:
            banner_scaled = f"bans{label_index}"
            filters.append(
                f"[{input_labels['top_banner']}]scale='min({self.config.top_banner_max_width},iw)':-2[{banner_scaled}]"
        )
            current_label, label_index = self._append_overlay_filter(
                filters=filters,
                base_label=current_label,
                overlay_label=banner_scaled,
                label_index=label_index + 1,
                x_expr="(W-w)/2",
                y_expr=str(self.config.top_banner_margin),
        )

        if "bottom_logo" in input_labels:
            logo_scaled = f"logs{label_index}"
            filters.append(
                f"[{input_labels['bottom_logo']}]scale='min({self.config.bottom_logo_max_width},iw)':-2[{logo_scaled}]"
        )
            current_label, label_index = self._append_overlay_filter(
                filters=filters,
                base_label=current_label,
                overlay_label=logo_scaled,
                label_index=label_index + 1,
                x_expr="(W-w)/2",
                y_expr=f"H-h-{self.config.bottom_logo_margin}",
        )

        return ";".join(filters), current_label, audio_label

    @staticmethod
    def _append_filter(
        filters: list[str],
        input_label: str,
        label_index: int,
        filter_expr: str,
    ) -> tuple[str, int]:
        output_label = f"v{label_index}"
        filters.append(f"[{input_label}]{filter_expr}[{output_label}]")
        return output_label, label_index + 1

    @staticmethod
    def _append_overlay_filter(
        filters: list[str],
        base_label: str,
        overlay_label: str,
        label_index: int,
        x_expr: str,
        y_expr: str,
        enable_expr: Optional[str] = None,
    ) -> tuple[str, int]:
        output_label = f"v{label_index}"
        enable_clause = f":enable='{enable_expr}'" if enable_expr else ""
        filters.append(
            f"[{base_label}][{overlay_label}]overlay=x='{x_expr}':y='{y_expr}'{enable_clause}[{output_label}]"
        )
        return output_label, label_index + 1

    def _append_duo_layout(
        self,
        filters: list[str],
        label_index: int,
        source_label: str,
        tracking: TrackingPath,
        x_expr: str,
        y_expr: str,
        x_expr_secondary: str,
        y_expr_secondary: str,
        max_x: int,
        duo_crop_width: int,
        duo_crop_height: int = 960,
    ) -> tuple[str, int]:
        top_src = f"duo_top_{label_index}"
        bot_src = f"duo_bot_{label_index}"
        base_out = f"v{label_index}"
        half_h = max(2, self.config.output_height // 2)
        target_w = self.config.output_width
        right_x_expr = x_expr_secondary or f"max(0,min({max_x},({max_x})-({x_expr})))"
        right_y_expr = y_expr_secondary or y_expr

        filters.append(
            f"[{source_label}]{self._crop_scale_cuda_chain(duo_crop_width, duo_crop_height, x_expr, y_expr, target_w, half_h)}[{top_src}]"
        )
        filters.append(
            f"[{source_label}]{self._crop_scale_cuda_chain(duo_crop_width, duo_crop_height, right_x_expr, right_y_expr, target_w, half_h)}[{bot_src}]"
        )
        filters.append(f"[{top_src}][{bot_src}]vstack=inputs=2[{base_out}]")
        return base_out, label_index + 1

    @staticmethod
    def _build_enable_expr(windows: list[tuple[float, float]]) -> Optional[str]:
        if not windows:
            return None
        parts = [
            f"between(t,{start:.3f},{end:.3f})" for start, end in windows if end > start
        ]
        if not parts:
            return None
        return "+".join(parts)

    @staticmethod
    def _append_jump_cut_filters(filters, keep_windows, fps=30):
        if not keep_windows:
            return "0:v", "0:a"
        # Sample-accurate audio trims and a common video frame grid. aselect
        # discards entire AAC frames and accumulates drift at every edit.
        n = len(keep_windows)
        filters.append(f"[0:v]fps={fps},split={n}" + "".join(f"[jv{i}]" for i in range(n)))
        filters.append(f"[0:a]asplit={n}" + "".join(f"[ja{i}]" for i in range(n)))
        for i, (start, end) in enumerate(keep_windows):
            filters.append(f"[jv{i}]trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS[v{i}cut]")
            filters.append(f"[ja{i}]atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS[a{i}cut]")
        filters.append("".join(f"[v{i}cut][a{i}cut]" for i in range(n)) + f"concat=n={n}:v=1:a=1[jcvin][jcain]")
        return "jcvin", "jcain"

    @staticmethod
    def _window_offsets(
        keep_windows: list[tuple[float, float]],
    ) -> list[tuple[float, float, float, float]]:
        offsets: list[tuple[float, float, float, float]] = []
        cursor = 0.0
        for start, end in sorted(keep_windows, key=lambda item: item[0]):
            if end <= start:
                continue
            duration = end - start
            offsets.append((start, end, cursor, cursor + duration))
            cursor += duration
        return offsets

    @staticmethod
    def _remap_points_to_jump_cuts(points, keep_windows):
        if not points or not keep_windows:
            return list(points or [])
        from bisect import bisect_right
        source = sorted(points, key=lambda p: p.t)
        times = [p.t for p in source]

        def at(t):
            idx = max(0, bisect_right(times, t)-1)
            p = source[idx]
            if idx+1 < len(source) and not source[idx+1].cut:
                nxt = source[idx+1]
                ratio = max(0, min(1, (t-p.t)/max(1e-9,nxt.t-p.t)))
                ratio = ratio*ratio*(3-2*ratio)
                return replace(p, t=t, x=p.x+(nxt.x-p.x)*ratio, y=p.y+(nxt.y-p.y)*ratio)
            return replace(p, t=t)

        mapped = []
        for start,end,dst_start,dst_end in ShortsRenderer._window_offsets(keep_windows):
            # Inject edit boundaries even if downsampled tracking has no point
            # inside the retained interval. Mark the next shot as a hard cut.
            mapped.append(replace(at(start), t=dst_start, cut=bool(mapped)))
            mapped.extend(replace(p, t=dst_start+p.t-start) for p in source if start < p.t < end)
            mapped.append(replace(at(end-1e-7), t=dst_end))
        dedup = {}
        for point in mapped:
            dedup[round(point.t,9)] = point
        return [dedup[key] for key in sorted(dedup)]

    @staticmethod
    def _remap_event_ranges(
        start_s: float,
        end_s: float,
        offsets: list[tuple[float, float, float, float]],
    ) -> list[tuple[float, float]]:
        mapped: list[tuple[float, float]] = []
        for src_start, src_end, dst_start, _ in offsets:
            overlap_start = max(start_s, src_start)
            overlap_end = min(end_s, src_end)
            if overlap_end <= overlap_start:
                continue
            mapped_start = dst_start + (overlap_start - src_start)
            mapped_end = dst_start + (overlap_end - src_start)
            if mapped_end > mapped_start:
                mapped.append((mapped_start, mapped_end))
        return mapped

    def _remap_windows_to_jump_cuts(
        self,
        windows: list[tuple[float, float]],
        keep_windows: list[tuple[float, float]],
    ) -> list[tuple[float, float]]:
        if not windows or not keep_windows:
            return list(windows)
        offsets = self._window_offsets(keep_windows)
        if not offsets:
            return list(windows)
        remapped: list[tuple[float, float]] = []
        for start_s, end_s in windows:
            remapped.extend(
                self._remap_event_ranges(
                    start_s=float(start_s),
                    end_s=float(end_s),
                    offsets=offsets,
            )
        )
        return remapped

    def _remap_subtitle_to_jump_cuts(
        self,
        source_subtitle_path: Path,
        target_subtitle_path: Path,
        keep_windows: list[tuple[float, float]],
    ) -> int:
        offsets = self._window_offsets(keep_windows)
        if not offsets:
            return 0
        subtitle = pysubs2.load(str(source_subtitle_path))
        remapped_events: list[pysubs2.SSAEvent] = []
        for event in subtitle.events:
            start_s = max(0.0, event.start / 1000.0)
            end_s = max(start_s, event.end / 1000.0)
            mapped_ranges = self._remap_event_ranges(
                start_s=start_s,
                end_s=end_s,
                offsets=offsets,
        )
            for mapped_start, mapped_end in mapped_ranges:
                remapped_event = copy.copy(event)
                remapped_event.start = int(round(mapped_start * 1000.0))
                remapped_event.end = max(
                    remapped_event.start + 1,
                    int(round(mapped_end * 1000.0)),
            )
                remapped_events.append(remapped_event)
        subtitle.events = sorted(remapped_events, key=lambda evt: (evt.start, evt.end))
        ensure_parent_dir(target_subtitle_path)
        subtitle.save(str(target_subtitle_path), format_="ass")
        # Also save SRT for jump-cut clip
        try:
            srt_path = Path(target_subtitle_path).with_suffix(".srt")
            srt_sub = copy.deepcopy(subtitle)
            for event in srt_sub.events:
                event.text = re.sub(r'\{[^}]+\}', '', event.text)
                event.text = event.text.replace(r'\N', ' ').replace('\n', ' ')
            srt_sub.save(str(srt_path), format_="srt")
        except Exception:
            LOGGER.exception("Failed to save SRT for jump-cut subtitles")
        return len(subtitle.events)

    def _build_jump_keep_windows(
        self,
        source_video: Path,
        clip: ClipCandidate,
        transcript: Transcript | None = None,
    ) -> list[tuple[float, float]]:
        silence_windows = self._detect_silence_windows(
            source_video=source_video, clip=clip
        )
        if not silence_windows:
            return []
        remove_windows: list[tuple[float, float]] = []
        padding = max(0.0, float(self.config.silence_padding_seconds))
        for start, end in silence_windows:
            cut_start = max(0.0, start + padding)
            cut_end = min(clip.duration, end - padding)
            # Low-volume syllables may look silent acoustically. Subtract every
            # known spoken word (plus a breath margin) before removing anything.
            pieces = [(cut_start,cut_end)]
            if transcript is not None:
                for word in transcript.words:
                    ws, we = word.start-clip.start-padding, word.end-clip.start+padding
                    pieces = [(a,b) for left,right in pieces for a,b in
                        ((left,min(right,ws)),(max(left,we),right)) if b>a]
            remove_windows.extend((a,b) for a,b in pieces if b-a>=.04)
        remove_windows = self._merge_windows(remove_windows, gap=0.025)
        keep_windows = self._invert_windows(
            duration=max(0.001, clip.duration),
            remove_windows=remove_windows,
            min_segment=0.05,
        )
        if not keep_windows:
            return []
        kept_duration = sum(end - start for start, end in keep_windows)
        if len(keep_windows) == 1 and kept_duration >= (clip.duration - 0.03):
            return []
        # Quantize once; subtitles, tracking and both streams share these cuts.
        fps = self.config.output_fps
        import math
        # Round kept windows outward: never round into a protected word.
        quantized = [(max(0,math.floor(a*fps)/fps),min(clip.duration,math.ceil(b*fps)/fps)) for a,b in keep_windows]
        return self._merge_windows([(a,b) for a,b in quantized if b > a], gap=0.0)

    def _detect_silence_windows(
        self,
        source_video: Path,
        clip: ClipCandidate,
    ) -> list[tuple[float, float]]:
        detect_cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        detect_cmd.extend(
            [
                "-v",
                "info",
                "-ss",
                f"{clip.start:.3f}",
                "-i",
                str(source_video),
                "-t",
                f"{max(0.001, clip.duration):.3f}",
                "-af",
                (
                    "silencedetect="
                    f"noise={float(self.config.silence_noise_db):.1f}dB:"
                    f"d={float(self.config.silence_min_duration_seconds):.3f}"
            ),
                "-f",
                "null",
                "-",
            ]
        )
        result = run_cmd(detect_cmd, check=False)
        if result.returncode != 0 and "silence_start" not in (
            result.stderr + result.stdout
        ):
            LOGGER.warning(
                "silencedetect failed for clip %.2f-%.2f", clip.start, clip.end
        )
            return []
        raw = "\n".join((result.stderr or "", result.stdout or ""))
        start_re = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
        end_re = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")
        windows: list[tuple[float, float]] = []
        open_start: Optional[float] = None

        def _normalize_time(value: float) -> float:
            t = float(value)
            if t > (clip.duration + 0.5) and t >= clip.start:
                t -= clip.start
            return max(0.0, min(clip.duration, t))

        for line in raw.splitlines():
            sm = start_re.search(line)
            if sm:
                open_start = _normalize_time(float(sm.group(1)))
                continue
            em = end_re.search(line)
            if em and open_start is not None:
                end_t = _normalize_time(float(em.group(1)))
                if end_t > open_start:
                    windows.append((open_start, end_t))
                open_start = None
        if open_start is not None and clip.duration > open_start:
            windows.append((open_start, clip.duration))
        min_silence = max(0.02, float(self.config.silence_min_duration_seconds))
        windows = [w for w in windows if (w[1] - w[0]) >= min_silence]
        return self._merge_windows(windows, gap=0.02)

    @staticmethod
    def _merge_windows(
        windows: list[tuple[float, float]],
        gap: float = 0.02,
    ) -> list[tuple[float, float]]:
        if not windows:
            return []
        ordered = sorted((start, end) for start, end in windows if end > start)
        merged: list[tuple[float, float]] = [ordered[0]]
        for start, end in ordered[1:]:
            prev_start, prev_end = merged[-1]
            if start <= (prev_end + gap):
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        return merged

    @staticmethod
    def _invert_windows(
        duration: float,
        remove_windows: list[tuple[float, float]],
        min_segment: float = 0.05,
    ) -> list[tuple[float, float]]:
        if duration <= 0.0:
            return []
        if not remove_windows:
            return [(0.0, duration)]
        merged = ShortsRenderer._merge_windows(remove_windows, gap=0.0)
        keep: list[tuple[float, float]] = []
        cursor = 0.0
        for start, end in merged:
            if start > cursor and (start - cursor) >= min_segment:
                keep.append((cursor, start))
            cursor = max(cursor, end)
        if duration > cursor and (duration - cursor) >= min_segment:
            keep.append((cursor, duration))
        return keep

    @staticmethod
    def _subtract_windows(
        base_windows: list[tuple[float, float]],
        minus_windows: list[tuple[float, float]],
        gap: float = 0.0,
    ) -> list[tuple[float, float]]:
        if not base_windows:
            return []
        if not minus_windows:
            return list(base_windows)
        cutouts = sorted(
            [
                (max(0.0, start - gap), end + gap)
                for start, end in minus_windows
                if end > start
            ],
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

    @staticmethod
    def _coerce_windows(
        windows: list[tuple[float, float]] | list[dict[str, float]],
        clip_duration: float,
        max_windows: int = 24,
        max_window_seconds: float = 0.0,
    ) -> list[tuple[float, float]]:
        out: list[tuple[float, float]] = []
        for item in windows or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                start = float(item[0])
                end = float(item[1])
            elif isinstance(item, dict):
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
            else:
                continue
            start = max(0.0, min(start, clip_duration))
            end = max(0.0, min(end, clip_duration))
            if end <= start:
                continue
            out.append((start, end))
        if not out:
            return []
        out.sort(key=lambda x: x[0])
        merged: list[tuple[float, float]] = [out[0]]
        for start, end in out[1:]:
            prev_start, prev_end = merged[-1]
            if start <= prev_end + 0.08:
                merged[-1] = (prev_start, max(prev_end, end))
            else:
                merged.append((start, end))
        clamped: list[tuple[float, float]] = []
        for start, end in merged:
            if max_window_seconds > 0.0 and (end - start) > max_window_seconds:
                mid = (start + end) * 0.5
                half = max_window_seconds * 0.5
                win_start = max(start, mid - half)
                win_end = min(end, mid + half)
                if win_end > win_start:
                    clamped.append((win_start, win_end))
            else:
                clamped.append((start, end))
        return clamped[:max_windows]

    @staticmethod
    def _normalize_layout_mode(value: str | None) -> str:
        valid = {"single", "reaction_split", "duo_split"}
        mode = (value or "single").strip().lower()
        return mode if mode in valid else "single"

    def _resolve_asset_path(self, value: str | None) -> Optional[Path]:
        raw = (value or "").strip()
        if not raw:
            return None
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (self.config.project_root / candidate).resolve()
        if not candidate.exists():
            LOGGER.warning("Skipping missing asset: %s", candidate)
            return None
        return candidate

    def _resolve_contextual_overlays(
        self,
        overlays: Sequence[ContextualOverlay | dict[str, Any]],
        clip: ClipCandidate,
    ) -> list[ResolvedContextualOverlay]:
        out: list[ResolvedContextualOverlay] = []
        for overlay in overlays:
            if isinstance(overlay, ContextualOverlay):
                asset_ref = overlay.asset_path
                start = overlay.start
                end = overlay.end
                y_offset = overlay.y_offset
                scale = overlay.scale
                max_width = overlay.max_width
            elif isinstance(overlay, dict):
                asset_ref = str(
                    overlay.get("asset_path", overlay.get("path", ""))
            ).strip()
                start = float(overlay.get("start", clip.start))
                end = float(overlay.get("end", clip.end))
                y_offset = int(
                    overlay.get("y_offset", self.config.contextual_overlay_chest_offset)
            )
                scale = float(overlay.get("scale", 1.0))
                max_width = int(
                    overlay.get("max_width", self.config.contextual_overlay_max_width)
            )
            else:
                continue
            asset_path = self._resolve_asset_path(asset_ref)
            if asset_path is None:
                continue
            start_rel = max(0.0, start - clip.start)
            end_rel = min(clip.duration, end - clip.start)
            if end_rel <= start_rel:
                continue
            out.append(
                ResolvedContextualOverlay(
                    path=asset_path,
                    start_rel=start_rel,
                    end_rel=end_rel,
                    y_offset=max(
                        0, int(y_offset or self.config.contextual_overlay_chest_offset)
                ),
                    scale=max(0.1, float(scale or 1.0)),
                    max_width=max(
                        64,
                        int(max_width or self.config.contextual_overlay_max_width),
                ),
            )
        )
        return out

    def _render_sfx_only_track(self, clip: ClipCandidate, output_path: Path) -> bool:
        audio_suffixes = (".mp3", ".wav", ".aac", ".ogg", ".flac", ".m4a")
        overlays = self._resolve_contextual_overlays(
            overlays=clip.contextual_overlays,
            clip=clip,
        )
        audio_overlays = [
            o for o in overlays if str(o.path).lower().endswith(audio_suffixes)
        ]
        if not audio_overlays:
            return False

        filter_parts: list[str] = []
        mix_inputs: list[str] = []
        for idx, overlay in enumerate(audio_overlays):
            delay_ms = max(0, int(round(overlay.start_rel * 1000)))
            duration_s = max(0.001, overlay.end_rel - overlay.start_rel)
            out_label = f"sfx{idx}"
            filter_parts.append(
                f"[{idx}:a]atrim=start=0:end={duration_s:.3f},asetpts=PTS-STARTPTS,"
                f"adelay={delay_ms}|{delay_ms},volume=0.8[{out_label}]"
        )
            mix_inputs.append(f"[{out_label}]")

        clip_duration = max(0.001, clip.duration)
        mix_joined = "".join(mix_inputs)
        filter_complex = (
            ";".join(filter_parts)
            + f";{mix_joined}amix=inputs={len(mix_inputs)}:duration=longest:dropout_transition=0,"
            f"atrim=start=0:end={clip_duration:.3f}[sfx_out]"
        )

        cmd: list[str] = (
            ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        )
        cmd.extend(["-y", "-threads", "4", "-v", "warning"])
        for overlay in audio_overlays:
            cmd.extend(["-i", str(overlay.path)])
        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                "[sfx_out]",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ]
        )
        try:
            run_cmd(cmd, check=True)
            return True
        except Exception as exc:
            LOGGER.warning("Could not render SFX-only track: %s", exc)
            return False

    def _fit_scene_filter_cpu(self, target_w: int, target_h: int) -> str:
        # Blur a small background, then upscale it. Blurring full 1080x1920
        # frames with 20 passes used to dominate the entire encode.
        w, h = max(2, int(target_w)), max(2, int(target_h))
        bw, bh = max(16, w//8//2*2), max(16, h//8//2*2)
        return (
            "split=2[bgraw][fgraw];"
            f"[bgraw]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
            f"crop={bw}:{bh},boxblur=6:1,scale={w}:{h}:flags=bilinear,"
            "eq=brightness=-0.15[bg];"
            f"[fgraw]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            "setsar=1[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )

    def _fit_scene_filter(self, target_w: int, target_h: int) -> str:
        if self._profile3_cpu_decode():
            return self._fit_scene_filter_cpu(target_w, target_h)
        frame_w = max(2, int(target_w))
        frame_h = max(2, int(target_h))
        if self.config.fit_scene_full_frame_mode:
            return (
                "split=2[bgraw][fgraw];"
                f"[bgraw]scale_cuda=-2:{frame_h}:interp_algo=4,hwdownload,format=nv12,"
                f"crop={frame_w}:{frame_h},"
                "boxblur=40:20,eq=brightness=-0.12[bg];"
                f"[fgraw]scale_cuda={frame_w}:{frame_h}:force_original_aspect_ratio=decrease,hwdownload,format=nv12,setsar=1[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
        # Keep fit-scene foreground near upper third (same intent as eye-line framing).
        y_anchor_ratio = max(0.0, min(0.60, float(self.config.tracking_eye_line_ratio)))
        return (
            "split=2[bgraw][fgraw];"
            f"[bgraw]scale_cuda=-2:{frame_h}:interp_algo=4,hwdownload,format=nv12,"
            f"crop={frame_w}:{frame_h},"
            "boxblur=40:20,eq=brightness=-0.2[bg];"
            f"[fgraw]scale_cuda='ceil(({frame_w}*1.2)/2)*2':-2:interp_algo=4,hwdownload,format=nv12[fgs];"
            "[fgs]pad=iw+6:ih+6:3:3:color=white@0.08[fgb];"
            "[fgb]split[fgmain][fgshadow];"
            "[fgshadow]boxblur=10:3,colorchannelmixer=aa=0.28[fgsh];"
            f"[bg][fgsh]overlay=x='trunc((W-w)/2)+4':y='trunc((H-h)*{y_anchor_ratio:.3f})+6'[bgsh];"
            f"[bgsh][fgmain]overlay=x='trunc((W-w)/2)':y='trunc((H-h)*{y_anchor_ratio:.3f})',setsar=1"
        )

    def _next_phrase_boundary_after(
        transcript: Transcript,
        clip: ClipCandidate,
        relative_time: float,
    ) -> Optional[float]:
        absolute_t = clip.start + relative_time
        for seg in transcript.segments:
            if seg.end <= absolute_t:
                continue
            if seg.start >= clip.end:
                break
            return max(0.0, seg.start - clip.start)
        return None

    def _is_static_scene(self, tracking: TrackingPath) -> bool:
        if tracking.fit_scene_mode:
            return True
        if len(tracking.points) < 3:
            return True
        deltas = [
            abs(cur.x - prev.x) + abs(cur.y - prev.y)
            for prev, cur in zip(tracking.points, tracking.points[1:])
        ]
        if not deltas:
            return True
        avg_motion = sum(deltas) / len(deltas)
        return avg_motion <= self.config.static_motion_threshold

    @staticmethod
    def _hex_to_ffmpeg_color(value: str) -> str:
        clean = value.strip().lstrip("#")
        if len(clean) != 6:
            clean = "FFFFFF"
        return f"0x{clean.upper()}"

    def _segment_tokens(text: str) -> set[str]:
        normalized = (text or "").strip().lower()
        normalized = unicodedata.normalize("NFKD", normalized)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^\w\s]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return {tok for tok in normalized.split() if len(tok) >= 4}

    @staticmethod
    def _write_render_metadata(
        meta_path: Path,
        clip_id: str,
        clip: ClipCandidate,
        slug: str,
        index: int,
        output_path: Path,
        source_video: Path,
        transcript_json_path: Optional[Path],
        subtitle_path: Optional[Path],
        subtitle_overlay_path: Optional[Path],
        tracking_path: Path,
    ) -> None:
        payload = {
            "clip_id": clip_id,
            "slug": slug,
            "index": int(index),
            "clip": clip.to_dict(),
            "clip_filename": output_path.name,
            "clip_path": str(output_path),
            "output_dir": str(output_path.parent),
            "source_video": str(source_video),
            "transcript_json": str(transcript_json_path)
            if transcript_json_path
            else None,
            "subtitle_filename": subtitle_path.name if subtitle_path else None,
            "subtitle_path": str(subtitle_path) if subtitle_path else None,
            "subtitle_overlay_filename": subtitle_overlay_path.name
            if subtitle_overlay_path
            else None,
            "subtitle_overlay_path": str(subtitle_overlay_path)
            if subtitle_overlay_path
            else None,
            "tracking_path": str(tracking_path),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(meta_path, payload)

    def _probe_media_duration(self, path: Path) -> float:
        result = run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            check=True,
        )
        try:
            return max(0.001, float((result.stdout or "0").strip()))
        except ValueError:
            return 0.001

    def _warn_muxed_av_duration_mismatch(
        self, output_path: Path, expected_s: float
    ) -> None:
        """Detect broken muxes where CUDA decode ended early but audio ran full length."""
        try:
            v = self._probe_stream_duration_seconds(output_path, "v:0")
            a = self._probe_stream_duration_seconds(output_path, "a:0")
        except Exception:
            return
        if v <= 0.1 or a <= 0.1:
            return
        drift = abs(v - a)
        if drift > 0.35:
            LOGGER.error(
                "Output A/V stream duration mismatch (video=%.3fs audio=%.3fs, expected≈%.3fs). "
                "File: %s",
                v,
                a,
                expected_s,
                output_path,
        )

    @staticmethod
    def _probe_stream_duration_seconds(path: Path, stream_spec: str) -> float:
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

    def _probe_media_dimensions(self, path: Path) -> tuple[int, int]:
        result = run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(path),
            ],
            check=True,
        )
        raw = (result.stdout or "").strip().lower().replace("x", " ")
        parts = [part for part in raw.split() if part]
        if len(parts) >= 2:
            try:
                width = max(2, int(float(parts[0])))
                height = max(2, int(float(parts[1])))
                return width, height
            except ValueError:
                pass
        raise RuntimeError(f"Could not probe video dimensions for {path}")

    def _build_center_tracking_path(
        self,
        video_path: Path,
        start_s: float,
        end_s: float,
    ) -> TrackingPath:
        src_w, src_h = self._probe_media_dimensions(video_path)
        crop_w = min(src_w, self.config.output_width)
        crop_h = min(src_h, self.config.output_height)
        if crop_w <= 0 or crop_h <= 0:
            raise RuntimeError(
                f"Invalid source dimensions for fallback tracking: {video_path}"
        )

        default_x = max(0.0, (src_w - crop_w) / 2.0)
        default_y = max(0.0, (src_h - crop_h) / 2.0)
        fallback_point = TrackingPoint(t=0.0, x=float(default_x), y=float(default_y), fx=(float(default_x) + crop_w/2.0)/src_w, fy=(float(default_y) + crop_h/2.0)/src_h)
        return TrackingPath(
            source_width=src_w,
            source_height=src_h,
            crop_width=crop_w,
            crop_height=crop_h,
            points=[fallback_point],
            secondary_points=[fallback_point],
            duo_left_points=[fallback_point],
            duo_right_points=[fallback_point],
            fit_scene_mode=True,
            face_confidence_avg=0.0,
            face_detect_ratio=0.0,
            multi_face_ratio=0.0,
            active_speaker_multi_face_ratio=0.0,
            active_speaker_multi_face_windows=[],
            active_speaker_single_windows=[],
        )

    def _render_subtitle_overlay_video(
        self,
        subtitle_path: Path,
        output_path: Path,
        duration_s: float,
    ) -> None:
        if not subtitle_path.exists():
            return
        # Log beginning of subtitle file so we can inspect ASS tags/style
        try:
            raw_sub = subtitle_path.read_text(encoding="utf-8")
            head_lines = "\n".join(raw_sub.splitlines()[:20])
            LOGGER.info("Overlay subtitles (%s) head:\n%s", subtitle_path, head_lines)
        except Exception:
            LOGGER.exception(
                "Failed to read subtitle file for overlay logging: %s", subtitle_path
        )
        ensure_parent_dir(output_path)
        sub_escaped = str(subtitle_path).replace("\\", "\\\\").replace(":", r"\:")
        codec = (self.config.captions_overlay_video_codec or "qtrle").strip().lower()
        detect_cmd = ["nice", "-n", "10", "ffmpeg"] if os.name != "nt" else ["ffmpeg"]
        detect_cmd.extend(
            [
                "-y",
                "-threads",
                "4",
                "-v",
                "warning",
                "-f",
                "lavfi",
                "-i",
                (
                    f"color=c=black@0.0:s={self.config.output_width}x{self.config.output_height}:"
                    f"r={self.config.output_fps}:d={max(0.001, duration_s):.3f}"
            ),
                "-vf",
                f"format=rgba,subtitles='{sub_escaped}',format=rgba",
                "-an",
            ]
        )
        if codec == "prores_ks":
            detect_cmd.extend(
            ["-c:v", "prores_ks", "-pix_fmt", "yuva444p10le", "-profile:v", "4444"]
        )
        else:
            detect_cmd.extend(["-c:v", "qtrle", "-pix_fmt", "argb"])
        detect_cmd.append(str(output_path))
        run_cmd(detect_cmd, check=True)

    def _new_tracker(self):
        return SpeakerTracker(
            model_dir=self.config.data_dir / "models",
            device=self.config.asd_device,
            cache_enabled=self.config.stage_cache_enabled,
            speaker_threshold=self.config.asd_threshold,
            speaker_margin=self.config.asd_margin,
            speaker_hold=self.config.asd_hold_seconds,
            ease_alpha=self.config.tracking_ease_alpha,
            max_pan_speed_px_per_s=self.config.tracking_max_pan_speed_px_per_s,
            imgsz=self.config.yolo_img_size,
            chunk_size_frames=self.config.yolo_chunk_size,
            eye_line_ratio=self.config.tracking_eye_line_ratio,
        )

    def _new_caption_builder(self) -> BrainRotCaptionBuilder:
        return BrainRotCaptionBuilder(style=self._caption_style)

    def _profile3_cpu_decode(self) -> bool:
        """Profile 3 uses CPU decode + GPU scale_cuda/NVENC."""
        return True

    def _crop_scale_cuda_chain(
        self,
        crop_w: int,
        crop_h: int,
        x_expr: str,
        y_expr: str,
        out_w: int,
        out_h: int,
    ) -> str:
        """Crop (expression) then scale; uses CPU for Profile 3, or GPU for others."""
        if self._profile3_cpu_decode():
            # MODO 100% CPU (Profile 3) - Resolve o erro de "hwupload" e é mais rápido
            # pois evita a cópia desnecessária do barramento PCIe apenas para o resize.
            return (
                f"format=yuv420p,crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}',"
                f"scale={out_w}:{out_h}:flags=lanczos,setsar=1"
        )
        
        # Modo Padrão com CUDA para outros perfis
        return (
            f"hwdownload,format=nv12,crop={crop_w}:{crop_h}:x='{x_expr}':y='{y_expr}',"
            f"hwupload,scale_cuda={out_w}:{out_h}:interp_algo=4,hwdownload,format=nv12,setsar=1"
        )

    def _build_encode_profiles(self) -> list[dict[str, Any]]:
        is_nvenc = "nvenc" in (self.config.ffmpeg_video_codec or "").lower()
        nvenc_tuning = (
            [
                "-spatial-aq",
                "1",
                "-temporal-aq",
                "1",
                "-rc-lookahead",
                "20",
            ]
            if is_nvenc
            else []
        )

        profiles: list[dict[str, Any]] = [
            {
                "hwaccel": self.config.ffmpeg_hwaccel,
                "nvenc_extra": nvenc_tuning,
            },
            {
                "hwaccel": self.config.ffmpeg_hwaccel,
                "nvenc_extra": [],
            },
        ]
        return profiles

    @staticmethod
    def _is_nvenc_oom_error(exc: RuntimeError) -> bool:
        text = str(exc).lower()
        markers = (
            "initializeencoder failed: out of memory",
            "cannot allocate memory",
            "error while opening encoder",
            "h264_nvenc",
            "out of memory (10)",
        )
        return any(marker in text for marker in markers)

    @staticmethod
    def _normalize_token(text: str) -> str:
        clean = (text or "").strip().lower()
        clean = unicodedata.normalize("NFKD", clean)
        clean = "".join(ch for ch in clean if not unicodedata.combining(ch))
        clean = re.sub(r"[^\w]+", "", clean)
        return clean

    @staticmethod
    @staticmethod
    @staticmethod
    def _build_coord_expr(points, axis: str, max_value: int) -> str:
        if max_value <= 0:
            return "0"
        if not points:
            return "0"
        if len(points) == 1:
            value = max(0.0, min(float(max_value), getattr(points[0], axis)))
            return f"{value:.3f}"

        parts = []
        n = len(points)
        for i in range(n - 1):
            p0 = points[i]
            p1 = points[i + 1]
            v0 = max(0.0, min(float(max_value), getattr(p0, axis)))
            v1 = max(0.0, min(float(max_value), getattr(p1, axis)))
            dt = max(0.001, p1.t - p0.t)
            progress = f"(t-{p0.t:.3f})/{dt:.3f}"
            smooth = f"(({progress})*({progress})*(3-2*({progress})))"
            lerp = f"{v0:.3f}" if getattr(p1, "cut", False) else f"({v0:.3f}+({v1 - v0:.3f})*{smooth})"
            
            # Use non-overlapping segments to avoid doubling values at boundaries.
            # Use lt() for the upper bound except for the very last segment.
            if i < n - 2:
                cond = f"gte(t,{p0.t:.3f})*lt(t,{p1.t:.3f})"
            else:
                cond = f"gte(t,{p0.t:.3f})*lt(t,{p1.t:.3f})"
            
            parts.append(f"{cond}*{lerp}")

        last_val = max(0.0, min(float(max_value), getattr(points[-1], axis)))
        parts.append(f"gte(t,{points[-1].t:.3f})*{last_val:.3f}")
        first_val = max(0.0, min(float(max_value), getattr(points[0], axis)))
        parts.append(f"lt(t,{points[0].t:.3f})*{first_val:.3f}")

        expr = "+".join(parts)
        return f"max(0,min({max_value},{expr}))"


    def _ensure_nvenc_encoder(self) -> None:
        codec = (self.config.ffmpeg_video_codec or "").strip()
        if not codec.endswith("_nvenc"):
            raise RuntimeError(
                "Native vertical renderer requires NVENC. "
                "Set FFMPEG_VIDEO_CODEC to h264_nvenc or hevc_nvenc."
        )
        result = run_cmd(["ffmpeg", "-hide_banner", "-encoders"], check=True)
        if codec not in result.stdout:
            raise RuntimeError(
                f"Requested NVENC codec '{codec}' is not available in this ffmpeg build."
        )

    def _ensure_hwaccel_cuda(self) -> None:
        if (self.config.ffmpeg_hwaccel or "").strip().lower() != "cuda":
            raise RuntimeError(
                "Native vertical renderer requires CUDA hwaccel. Set FFMPEG_HWACCEL=cuda."
        )
        result = run_cmd(["ffmpeg", "-hide_banner", "-hwaccels"], check=True)
        if "cuda" not in result.stdout.lower():
            raise RuntimeError(
                "FFmpeg CUDA hwaccel is not available in this ffmpeg build."
        )
