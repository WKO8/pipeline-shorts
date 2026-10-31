from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from src.post_processor import apply_cinematic_zoom

from .quality_review import refine_render_clips, write_quality_review
from .config import AppConfig
from .curator import CuratorResult, OllamaCurator, get_curator
from .downloader import DownloadResult, YouTubeDownloader
from .models import ClipCandidate, PipelineArtifacts, Transcript
from .renderer import RenderResult, ShortsRenderer
from .transcriber import WhisperTranscriber, release_gpu_memory
from .utils import (
    ensure_dir,
    new_run_dir,
    preserve_existing,
    file_stem_sanitized,
    require_binaries,
    safe_unlink,
    setup_logging,
    write_json,
)

LOGGER = logging.getLogger(__name__)


def _profile_clips_dir(config: AppConfig, slug: str) -> Path:
    return config.clips_dir / f"profile_{int(config.pipeline_profile)}" / slug


@dataclass(slots=True)
class PipelineRequest:
    """
    Input payload for the orchestrator.
    """

    url: str
    slug: Optional[str] = None
    max_clips: Optional[int] = None
    language: Optional[str] = None


@dataclass(slots=True)
class PipelineResult:
    """
    Full output of one pipeline execution.
    """

    request: PipelineRequest
    artifacts: PipelineArtifacts
    download: DownloadResult
    transcript: Transcript
    curator: CuratorResult
    render_results: List[RenderResult]

    def to_dict(self) -> dict:
        return {
            "request": {
                "url": self.request.url,
                "slug": self.request.slug,
                "max_clips": self.request.max_clips,
                "language": self.request.language,
            },
            "artifacts": self.artifacts.to_dict(),
            "download": self.download.to_dict(),
            "transcript": self.transcript.to_dict(),
            "curation": {
                "parsed_ok": self.curator.parsed_ok,
                "warning": self.curator.warning,
                "clips": [c.to_dict() for c in self.curator.clips],
            },
            "render_results": [r.to_dict() for r in self.render_results],
        }


class ShortsPipelineService:
    """
    End-to-end orchestrator for:
      1) YouTube download (video + audio extraction),
      2) Whisper transcription with word timestamps,
      3) LLM curation via local Ollama,
      4) Vertical short rendering with subtitles.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.downloader = YouTubeDownloader(config=config)
        self.transcriber = WhisperTranscriber(config=config)
        self.curator = get_curator(config=config)
        self.renderer = ShortsRenderer(config=config)

    # ------------------------
    # Public API
    # ------------------------

    def run(self, request: PipelineRequest) -> PipelineResult:
        """
        Execute full pipeline and return structured result.
        """
        self._bootstrap()

        slug = self._resolve_slug(request)
        artifacts = PipelineArtifacts(working_dir=self.config.project_root)

        LOGGER.info("Starting shorts pipeline | slug=%s", slug)
        LOGGER.info("URL: %s", request.url)

        # 1) Download
        download = self.downloader.download(url=request.url, slug=slug)
        artifacts.raw_video = download.video_path
        artifacts.raw_audio = download.audio_path

        # 2) Transcribe
        release_gpu_memory()
        original_whisper_device = self.config.whisper_device
        original_whisper_compute = self.config.whisper_compute_type
        if self.config.stage1_cpu_only:
            self.config.whisper_device = "cpu"
            self.config.whisper_compute_type = "int8"
            LOGGER.info("Stage-1 CPU mode enabled for transcription.")

        transcript_json_path = self.config.transcripts_dir / f"{slug}.transcript.json"
        try:
            transcript = self.transcriber.transcribe(
                audio_path=download.audio_path,
                output_json_path=transcript_json_path,
                language=request.language or self.config.whisper_language,
                defer_alignment=self.config.selective_alignment,
            )
        finally:
            self.transcriber.unload_model()
            self.config.whisper_device = original_whisper_device
            self.config.whisper_compute_type = original_whisper_compute
        transcript.source_video = download.video_path
        artifacts.transcript_json = transcript_json_path

        # 3) Curate clips with Ollama
        try:
            curator_result = self.curator.curate(transcript)
        finally:
            self.curator.unload_model()
        clips = self._select_clips(
            curator_result.clips,
            max_clips=request.max_clips,
            transcript_duration=transcript.end,
        )

        clips_json_path = self.config.transcripts_dir / f"{slug}.clips.json"
        write_json(
            clips_json_path,
            {
                "source_url": request.url,
                "slug": slug,
                "parsed_ok": curator_result.parsed_ok,
                "warning": curator_result.warning,
                "api_reservations": self.curator.budget.calls if hasattr(self.curator, "budget") else [],
                "clips": [c.to_dict() for c in clips],
            },
        )
        artifacts.clips_json = clips_json_path

        if not clips:
            raise RuntimeError(
                "No valid clips were produced by curation. "
                "Try increasing transcript quality or relaxing duration limits."
            )

        output_dir = new_run_dir(_profile_clips_dir(config=self.config, slug=slug))
        if self.config.whisper_alignment and self.config.selective_alignment:
            transcript = self.transcriber.align_selected(transcript, download.audio_path, clips)
            artifacts.transcript_json = output_dir / f"{slug}.aligned.transcript.json"
            write_json(artifacts.transcript_json, transcript.to_dict())
            clips = refine_render_clips(clips, transcript, self.config)
            if not clips:
                raise RuntimeError("No complete clips fit duration limits after word-boundary refinement")
        artifacts.transcript_json = output_dir / f"{slug}.render.transcript.json"
        write_json(artifacts.transcript_json, transcript.to_dict())
        write_json(output_dir / "selected-clips.json", {"clips": [c.to_dict() for c in clips]})
        write_quality_review(output_dir, transcript, clips, source_video=download.video_path)
        # 4) Render clips
        release_gpu_memory()
        render_results = self.renderer.render_clips(
            source_video=download.video_path,
            transcript=transcript,
            clips=clips,
            output_dir=output_dir,
            slug=slug,
            transcript_json_path=artifacts.transcript_json,
        )
        artifacts.rendered_clips = [r.output_path for r in render_results]
        write_quality_review(output_dir, transcript, clips, render_results, download.video_path)
        release_gpu_memory()

        if self.config.cinematic_zoom:
            LOGGER.info("Applying post-processing cinematic zoom to rendered clips...")
            import shutil
            for result in render_results:
                final_path = Path(result.output_path)
                zoomed_path = final_path.with_stem(final_path.stem + "_zoomed")
                try:
                    apply_cinematic_zoom(
                        final_path, 
                        zoomed_path, 
                        tracking_points=result.tracking_data.points if result.tracking_data else None,
                        eye_line_ratio=self.config.tracking_eye_line_ratio
                    )
                    preserve_existing(final_path)
                    shutil.copy2(zoomed_path, final_path)
                except Exception as e:
                    LOGGER.error(f"Failed to apply cinematic zoom to {final_path}: {e}")
                    if zoomed_path.exists():
                        LOGGER.info("Preserved zoom output: %s", zoomed_path)

        # Cleanup
        self._cleanup_intermediate(slug=slug)

        result = PipelineResult(
            request=request,
            artifacts=artifacts,
            download=download,
            transcript=transcript,
            curator=CuratorResult(
                clips=clips,
                raw_response=curator_result.raw_response,
                parsed_ok=curator_result.parsed_ok,
                warning=curator_result.warning,
            ),
            render_results=render_results,
        )

        # Save final run manifest
        manifest_path = output_dir / f"{slug}.manifest.json"
        write_json(manifest_path, result.to_dict())
        LOGGER.info("Pipeline complete. Manifest: %s", manifest_path)
        LOGGER.info("Rendered %d clips.", len(render_results))

        return result

    # ------------------------
    # Bootstrap / validation
    # ------------------------

    def _bootstrap(self) -> None:
        setup_logging(self.config.log_level)
        self.config.ensure_directories()
        ensure_dir(self.config.clips_dir)

        # Runtime binary requirements
        require_binaries(["yt-dlp", "ffmpeg"])

        LOGGER.debug("Project root: %s", self.config.project_root)

    def _resolve_slug(self, request: PipelineRequest) -> str:
        raw = (request.slug or "").strip()
        if not raw:
            # fallback based on url + project basename
            raw = f"{self.config.project_name}"
        return file_stem_sanitized(raw)

    # ------------------------
    # Clip post-processing
    # ------------------------

    def _select_clips(
        self,
        clips: List[ClipCandidate],
        max_clips: Optional[int],
        transcript_duration: float,
    ) -> List[ClipCandidate]:
        """
        Final safety checks:
          - clamp to transcript/video duration,
          - enforce min/max duration from config,
          - deduplicate overlaps lightly,
          - cap to desired count.
        """
        desired_count = max_clips if (max_clips and max_clips > 0) else len(clips)
        min_d = float(self.config.clip_min_seconds)
        max_d = float(self.config.clip_max_seconds)

        normalized: List[ClipCandidate] = []
        for clip in clips:
            c = clip.clamp(transcript_duration if transcript_duration > 0 else clip.end)
            if c.end <= c.start:
                continue

            if not min_d <= c.duration <= max_d:
                continue
            normalized.append(c)

        # Sort by score desc then chronological
        normalized.sort(key=lambda x: (-x.score, x.start))

        # light overlap filter
        selected: List[ClipCandidate] = []
        for cand in normalized:
            if self._overlaps_too_much(cand, selected, threshold=0.75):
                continue
            selected.append(cand)
            if len(selected) >= desired_count:
                break

        selected.sort(key=lambda c: c.start)
        return selected[:desired_count]

    def _overlaps_too_much(
        self,
        candidate: ClipCandidate,
        chosen: List[ClipCandidate],
        threshold: float,
    ) -> bool:
        for other in chosen:
            if candidate.source_spans or other.source_spans:
                from .narrative import source_overlap
                if source_overlap(candidate, other) >= threshold:
                    return True
                continue
            inter = self._intersection(
                candidate.start, candidate.end, other.start, other.end
            )
            shorter = min(candidate.duration, other.duration)
            if shorter <= 0:
                continue
            if (inter / shorter) >= threshold:
                return True
        return False

    @staticmethod
    def _intersection(a1: float, a2: float, b1: float, b2: float) -> float:
        left = max(a1, b1)
        right = min(a2, b2)
        return max(0.0, right - left)

    # ------------------------
    # Cleanup
    # ------------------------

    def _cleanup_intermediate(self, slug: str) -> None:
        if self.config.keep_intermediate_files:
            return

        # Keep final clips + transcript + clips json manifest.
        # Remove temporary crop/subtitle/info files in temp folder.
        temp_patterns = [
            f"{slug}*.ass",
            f"{slug}*.srt",
            f"{slug}*.crop.json",
            f"{slug}*.info.json",
        ]

        for pattern in temp_patterns:
            for path in self.config.temp_dir.glob(pattern):
                safe_unlink(path)


def run_pipeline(config: AppConfig, request: PipelineRequest) -> PipelineResult:
    """
    Convenience function for one-shot orchestration.
    """
    service = ShortsPipelineService(config=config)
    return service.run(request)
