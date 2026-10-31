from __future__ import annotations

import ctypes
import gc
import logging
import math
import os
import site
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Optional

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

from .config import AppConfig
from .models import Transcript, TranscriptSegment, WordTimestamp
from .utils import ensure_parent_dir, write_json

LOGGER = logging.getLogger(__name__)


class WhisperTranscriber:
    """
    Local Whisper transcription using faster-whisper with word-level timestamps.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._model: Optional[WhisperModel] = None
        self._active_device: str | None = None
        self._active_compute_type: str | None = None

    @staticmethod
    def _prepare_cuda_runtime() -> None:
        """
        Ensure CUDA runtime libraries installed via pip (nvidia-* packages)
        are visible to the current process.
        """
        base_paths: list[str] = []
        for p in site.getsitepackages():
            if Path(p).exists():
                base_paths.append(p)
        user_site = site.getusersitepackages()
        if Path(user_site).exists():
            base_paths.append(user_site)

        lib_dirs: list[Path] = []
        for base in base_paths:
            root = Path(base) / "nvidia"
            for rel in ("cublas/lib", "cuda_runtime/lib", "cudnn/lib"):
                d = root / rel
                if d.exists():
                    lib_dirs.append(d)

        extra_system_dirs = [
            Path("/usr/local/lib/ollama/cuda_v12"),
            Path("/usr/local/cuda-12/lib64"),
            Path("/usr/local/cuda/lib64"),
            Path("/usr/lib/x86_64-linux-gnu"),
        ]
        for ed in extra_system_dirs:
            if ed.exists() and ed not in lib_dirs:
                lib_dirs.append(ed)

        if not lib_dirs:
            return

        env_paths = [str(d) for d in lib_dirs]
        current_ld = os.environ.get("LD_LIBRARY_PATH", "")
        if current_ld:
            env_paths.append(current_ld)
        os.environ["LD_LIBRARY_PATH"] = ":".join(env_paths)

        for lib_dir in lib_dirs:
            for lib_name in (
                "libcudart.so.12",
                "libcublas.so.12",
                "libcublasLt.so.12",
                "libcudnn.so.9",
                "libcudnn.so.8",
            ):
                lib_path = lib_dir / lib_name
                if lib_path.exists():
                    try:
                        ctypes.CDLL(str(lib_path), mode=ctypes.RTLD_GLOBAL)
                    except Exception:
                        pass

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            requested_device = self.config.whisper_device
            requested_compute = self.config.whisper_compute_type

            candidates: list[tuple[str, str]] = []
            if requested_device in {"auto", "cuda"}:
                if requested_compute:
                    candidates.append(("cuda", requested_compute))
                candidates.extend(
                    [
                        ("cuda", "float16"),
                        ("cuda", "int8_float16"),
                        ("cuda", "int8"),
                    ]
                )
            candidates.append(("cpu", requested_compute if requested_compute in {"int8", "float32"} else "int8"))
            candidates.append(("cpu", "int8"))

            # remove duplicados preservando ordem
            dedup: list[tuple[str, str]] = []
            for c in candidates:
                if c not in dedup:
                    dedup.append(c)
            candidates = dedup

            last_exc: Exception | None = None
            for device, compute_type in candidates:
                try:
                    if device == "cuda":
                        self._prepare_cuda_runtime()
                    from faster_whisper import WhisperModel

                    LOGGER.info(
                        "Loading Whisper model '%s' (device=%s, compute_type=%s)",
                        self.config.whisper_model,
                        device,
                        compute_type,
                    )
                    self._model = WhisperModel(
                        self.config.whisper_model,
                        device=device,
                        compute_type=compute_type,
                        download_root=os.path.join(
                            self.config.data_dir, "models", "whisper"
                        ),
                    )
                    self._active_device = device
                    self._active_compute_type = compute_type
                    LOGGER.info(
                        "Whisper model loaded successfully (device=%s, compute_type=%s)",
                        device,
                        compute_type,
                    )
                    return self._model
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    LOGGER.warning(
                        "Failed loading Whisper with device=%s compute_type=%s: %s",
                        device,
                        compute_type,
                        exc,
                    )

            raise RuntimeError(
                f"Could not load Whisper model '{self.config.whisper_model}' "
                f"with any backend candidate. Last error: {last_exc}"
            )
        return self._model

    def unload_model(self) -> None:
        """Explicitly release model weights and free GPU memory."""
        self._model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception:
            pass
        LOGGER.info("Whisper model unloaded and VRAM released.")

    def transcribe(
        self,
        audio_path: str | Path,
        output_json_path: str | Path | None = None,
        language: str | None = None,
        defer_alignment: bool = False,
    ) -> Transcript:
        """
        Transcribe audio and return normalized Transcript with word timestamps.
        """
        audio_path = Path(audio_path).resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        language = language or self.config.whisper_language

        from .stage_cache import StageCache, digest, file_digest, package_versions
        cache = StageCache(self.config.data_dir, self.config.stage_cache_enabled)
        key = digest(dict(version=1, audio=file_digest(audio_path), language=language,
                          model=self.config.whisper_model, device=self.config.whisper_device,
                          compute=self.config.whisper_compute_type,
                          words=self.config.whisper_word_timestamps,
                          packages=package_versions("faster-whisper", "ctranslate2")))
        cached = cache.get("asr", key)
        if cached is not None:
            transcript = Transcript.from_dict(cached)
            transcript.source_audio = audio_path
            if self.config.whisper_alignment and not defer_alignment:
                self.unload_model()
                transcript = self.align_selected(transcript, audio_path)
            if output_json_path is not None:
                write_json(output_json_path, transcript.to_dict())
            return transcript
        LOGGER.info("Transcribing: %s", audio_path)
        degraded_retry = False
        try:
            transcript = self._transcribe_once(
                audio_path=audio_path,
                language=language,
                beam_size=5,
                condition_on_previous_text=False,
            )
        except RuntimeError as exc:
            if self._should_retry_on_low_mem_cuda(exc):
                degraded_retry = True
                LOGGER.warning(
                    "CUDA OOM during transcription (%s). Retrying with low-memory CUDA settings.",
                    exc,
                )
                release_gpu_memory()
                self._model = None
                self._active_device = "cuda"
                self._active_compute_type = "int8"
                self.config.whisper_device = "cuda"
                self.config.whisper_compute_type = "int8"
                try:
                    transcript = self._transcribe_once(
                        audio_path=audio_path,
                        language=language,
                        beam_size=1,
                        condition_on_previous_text=False,
                    )
                except RuntimeError as low_mem_exc:
                    LOGGER.error("Low-memory CUDA retry failed: %s", low_mem_exc)
                    raise RuntimeError(
                        f"Transcription failed on GPU even with low-memory settings: {low_mem_exc}"
                    ) from low_mem_exc
            elif self._should_retry_on_cpu(exc):
                LOGGER.error(
                    "CUDA error during transcription: %s. CPU fallback is disabled.",
                    exc,
                )
                raise RuntimeError(
                    f"Transcription failed on GPU and CPU fallback is disabled: {exc}"
                ) from exc
            else:
                raise

        if not degraded_retry:
            cache.put("asr", key, transcript.to_dict())
        if self.config.whisper_alignment and not defer_alignment:
            self.unload_model()  # CTranslate2 weights must go before wav2vec2.
            transcript = self.align_selected(transcript, audio_path)

        LOGGER.info(
            "Transcription complete | language=%s | segments=%d | words=%d | duration=%.2fs",
            transcript.language,
            len(transcript.segments),
            len(transcript.words),
            transcript.duration,
        )

        if output_json_path is not None:
            output_json_path = Path(output_json_path)
            ensure_parent_dir(output_json_path)
            write_json(output_json_path, transcript.to_dict())
            LOGGER.info("Saved transcript JSON: %s", output_json_path)

        return transcript

    def _transcribe_once(
        self,
        audio_path: Path,
        language: str | None,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> Transcript:
        segments_iter, info = self.model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=True,
            word_timestamps=self.config.whisper_word_timestamps,
            condition_on_previous_text=condition_on_previous_text,
            temperature=0.0,
        )
        transcript = Transcript(
            language=info.language or language or "unknown",
            timing_source="faster-whisper",
            source_audio=audio_path,
            segments=[],
        )
        transcript.segments = list(self._convert_segments(segments_iter))
        return transcript

    @staticmethod
    def _alignment_words(segment, aligned, lower, upper):
        """Reject incomplete, non-finite or overlapping alignment output."""
        raw = [w for sentence in aligned.get("segments", [])
               for w in sentence.get("words", []) if w.get("word", "").strip()]
        if "".join(w["word"].strip() for w in raw) != "".join(segment.text.split()):
            return [], False
        words = []
        used_original = False
        for idx, word in enumerate(raw):
            text = word["word"].strip()
            start, end = word.get("start"), word.get("end")
            if start is None or end is None:
                original = segment.words[idx] if idx < len(segment.words) else None
                if original is None or original.text.strip() != text:
                    return [], False
                start, end = original.start, original.end
                probability = original.probability
                used_original = True
            else:
                probability = word.get("score")
            start, end = float(start), float(end)
            if (not math.isfinite(start) or not math.isfinite(end)
                    or start < lower - 0.04 or end > upper + 0.04 or end <= start
                    or (words and start < words[-1].end)):
                return [], False
            # WhisperX frame-to-time conversion can overshoot by one CTC frame.
            start, end = max(lower, start), min(upper, end)
            if end <= start:
                return [], False
            words.append(WordTimestamp(text=text, start=start, end=end, probability=probability))
        return words, used_original

    def align_selected(self, transcript, audio_path, clips=None):
        """Align each selected source segment once, retaining absolute timestamps."""
        from .stage_cache import StageCache, digest, file_digest, package_versions
        selected = None if clips is None else {
            s.id for s in transcript.segments
            if any(s.end > a and s.start < b for c in clips for a,b in c.source_ranges)}
        if selected == set():
            return transcript
        cache = StageCache(self.config.data_dir, self.config.stage_cache_enabled)
        # Include all boundaries: retry margins depend on adjacent segments.
        key = digest(dict(version=2, audio=file_digest(Path(audio_path)),
                          transcript=transcript.to_dict(), ids=sorted(selected) if selected is not None else None,
                          model=self.config.whisper_alignment_model, language=transcript.language,
                          padding=self.config.whisper_alignment_retry_padding,
                          device=self.config.whisper_device, cpu_only=self.config.stage1_cpu_only,
                          packages=package_versions("whisperx", "transformers", "torch")))
        cached = cache.get("alignment", key)
        if cached is not None:
            return Transcript.from_dict(cached)
        if selected is None:
            result = self.align_transcript(transcript, Path(audio_path))
        else:
            LOGGER.info("Selective alignment: %d/%d segments", len(selected), len(transcript.segments))
            result = self.align_transcript(transcript, Path(audio_path), selected_ids=selected)
        # Failed alignments can be retried next time without retranscribing.
        if result.timing_source in {"whisperx", "whisperx-partial"}:
            cache.put("alignment", key, result.to_dict())
        return result

    def align_transcript(self, transcript: Transcript, audio_path: Path, selected_ids=None) -> Transcript:
        """Keep ASR timestamps unless a complete, ordered alignment is available."""
        model_a = None
        aligned_count = mixed_count = recovered_count = 0
        try:
            import torch
            import whisperx
            device = "cuda" if self.config.whisper_device != "cpu" and not self.config.stage1_cpu_only and torch.cuda.is_available() else "cpu"
            LOGGER.info("Forced alignment (%s, %s)", transcript.language, device)
            kwargs = dict(language_code=transcript.language, device=device,
                          model_dir=str(self.config.data_dir / "models/alignment"))
            if self.config.whisper_alignment_model:
                kwargs["model_name"] = self.config.whisper_alignment_model
            model_a, metadata = whisperx.load_align_model(**kwargs)
            audio = whisperx.load_audio(str(audio_path))
            audio_duration = len(audio) / 16000
            bounds = [(s.start, s.end) for s in transcript.segments]
            for index, segment in enumerate(transcript.segments):
                if selected_ids is not None and segment.id not in selected_ids:
                    continue
                lower, upper = max(0.0, segment.start), min(audio_duration, segment.end)
                if upper <= lower or not segment.text.strip():
                    continue
                def attempt(start, end):
                    result = whisperx.align(
                        [{"start": start, "end": end, "text": segment.text}],
                        model_a, metadata, audio, device, return_char_alignments=False)
                    return self._alignment_words(segment, result, start, end)

                words, mixed = attempt(lower, upper)
                if not words and self.config.whisper_alignment_retry_padding > 0:
                    # Retry only failures and never borrow neighbouring speech.
                    padding = self.config.whisper_alignment_retry_padding
                    previous_end = bounds[index - 1][1] if index else 0.0
                    next_start = bounds[index + 1][0] if index + 1 < len(bounds) else audio_duration
                    # Split each gap at its midpoint so retries cannot overlap.
                    retry_start = max(0.0, lower - padding, min(lower, (previous_end + lower) / 2))
                    retry_end = min(audio_duration, upper + padding, max(upper, (upper + next_start) / 2))
                    if retry_start < lower or retry_end > upper:
                        words, mixed = attempt(retry_start, retry_end)
                        if words:
                            recovered_count += 1
                if words:
                    segment.words = words
                    segment.start = min(segment.start, words[0].start)
                    segment.end = max(segment.end, words[-1].end)
                    aligned_count += 1
                    mixed_count += int(mixed)
        except Exception:
            LOGGER.exception("Forced alignment interrupted; retaining ASR timestamps for remaining segments")
        finally:
            del model_a
            release_gpu_memory()
        if aligned_count:
            transcript.timing_source = (
                "whisperx" if aligned_count == len(transcript.segments) and not mixed_count
                else "whisperx-partial")
        LOGGER.info("Alignment: %d/%d accepted, %d recovered, %d mixed, %d ASR fallback",
                    aligned_count, len(transcript.segments), recovered_count, mixed_count,
                    len(transcript.segments) - aligned_count)
        return transcript

    def _retry_on_cpu(self, audio_path: Path, language: str | None) -> Transcript:
        release_gpu_memory()
        self._model = None
        self._active_device = "cpu"
        self._active_compute_type = "int8"
        self.config.whisper_device = "cpu"
        self.config.whisper_compute_type = "int8"
        return self._transcribe_once(
            audio_path=audio_path,
            language=language,
            beam_size=5,
            condition_on_previous_text=False,
        )

    def _convert_segments(self, segments_iter: Iterable) -> Iterable[TranscriptSegment]:
        for idx, seg in enumerate(segments_iter):
            words = []
            raw_words = getattr(seg, "words", None) or []
            for w in raw_words:
                start = float(getattr(w, "start", 0.0) or 0.0)
                end = float(getattr(w, "end", start) or start)
                if end < start:
                    end = start
                words.append(
                    WordTimestamp(
                        text=(getattr(w, "word", "") or "").strip(),
                        start=start,
                        end=end,
                        probability=getattr(w, "probability", None),
                    )
                )

            seg_start = float(getattr(seg, "start", 0.0) or 0.0)
            seg_end = float(getattr(seg, "end", seg_start) or seg_start)
            if seg_end < seg_start:
                seg_end = seg_start

            yield TranscriptSegment(
                id=idx,
                start=seg_start,
                end=seg_end,
                text=(getattr(seg, "text", "") or "").strip(),
                words=words,
            )

    def _should_retry_on_cpu(self, exc: RuntimeError) -> bool:
        if self._active_device != "cuda":
            return False
        error_text = str(exc).lower()
        cuda_missing_markers = (
            "libcublas.so",
            "libcuda.so",
            "libcudnn",
            "cannot be loaded",
        )
        if any(marker in error_text for marker in cuda_missing_markers):
            return True
        return self._should_retry_on_low_mem_cuda(exc)

    def _should_retry_on_low_mem_cuda(self, exc: RuntimeError) -> bool:
        if self._active_device != "cuda":
            return False
        error_text = str(exc).lower()
        oom_markers = (
            "out of memory",
            "cuda failed with error out of memory",
            "cublas_status_alloc_failed",
            "cuda_error_out_of_memory",
        )
        return any(marker in error_text for marker in oom_markers)


def transcribe_audio(
    config: AppConfig,
    audio_path: str | Path,
    output_json_path: str | Path | None = None,
    language: str | None = None,
    defer_alignment: bool = False,
) -> Transcript:
    """
    Convenience function for one-shot transcription with automatic memory cleanup.
    """
    transcriber = WhisperTranscriber(config)
    try:
        return transcriber.transcribe(
            audio_path=audio_path,
            output_json_path=output_json_path,
            language=language,
            defer_alignment=defer_alignment,
        )
    finally:
        transcriber.unload_model()


def release_gpu_memory() -> None:
    """
    Best-effort cleanup to free CUDA VRAM before render stage.
    """
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    except Exception:  # noqa: BLE001
        return
