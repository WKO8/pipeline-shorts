from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .utils import ensure_dir, run_cmd, safe_rmtree

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class UpscaleResult:
    output_path: Path
    scale: int
    model: str
    backend: str
    face_enhance: bool
    face_model: str
    tile_size: int
    threads: str
    gpu_id: str


class RealEsrganNcnnUpscaler:
    """
    Applies Real-ESRGAN NCNN Vulkan to a rendered clip.

    The pipeline keeps this as an optional post-process step for selected clips,
    avoiding global VRAM pressure when rendering batches.
    """

    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg

    def upscale_video_inplace(
        self,
        clip_path: str | Path,
        model_name: str | None = None,
        scale: int | None = None,
        tile_size: int | None = None,
        threads: str | None = None,
        gpu_id: str | None = None,
    ) -> UpscaleResult:
        source = Path(clip_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"Clip not found for upscaling: {source}")

        selected_model = (model_name or self.cfg.upscaling_model).strip()
        selected_scale = int(scale if scale is not None else self.cfg.upscaling_scale)
        selected_tile = int(
            tile_size if tile_size is not None else self.cfg.upscaling_tile_size
        )
        selected_threads = (threads or self.cfg.upscaling_threads).strip()
        selected_gpu = (gpu_id or self.cfg.upscaling_gpu_id).strip()

        selected_face_enhance = bool(self.cfg.upscaling_face_enhance)
        selected_face_model = (
            (self.cfg.upscaling_face_model or "gfpgan").strip().lower()
        )
        if selected_face_model not in {"gfpgan", "codeformer"}:
            raise ValueError("UPSCALING_FACE_MODEL must be one of: gfpgan, codeformer")

        # face-only-codeformer: skip ESRGAN entirely, restore faces at original resolution
        if self.cfg.upscaling_face_only_codeformer:
            return self._run_face_only_codeformer(
                source=source,
                selected_gpu=selected_gpu,
                selected_tile=selected_tile,
            )

        if selected_face_enhance:
            return self._upscale_with_python_face_enhance(
                source=source,
                selected_model=selected_model,
                selected_scale=selected_scale,
                selected_tile=selected_tile,
                selected_gpu=selected_gpu,
                selected_face_model=selected_face_model,
                selected_threads=selected_threads,
            )

        model_dir = self._resolve_model_dir()
        self._validate_model_files(
            model_dir=model_dir,
            model_name=selected_model,
            scale=selected_scale,
        )

        if selected_scale not in {2, 3, 4}:
            raise ValueError("Upscaling scale must be one of: 2, 3, 4")
        if selected_tile != 0 and selected_tile < 32:
            raise ValueError("Upscaling tile size must be 0 (auto) or >= 32")
        if not selected_threads:
            raise ValueError("Upscaling threads cannot be empty.")

        tmp_root = ensure_dir(self.cfg.temp_dir / "upscaling" / source.stem)
        frames_in = ensure_dir(tmp_root / "frames_in")
        frames_out = ensure_dir(tmp_root / "frames_out")
        audio_path = tmp_root / "audio.m4a"
        reconstructed_path = tmp_root / f"{source.stem}.upscaled.mp4"

        probe = self._probe_video(source)
        fps = float(probe.get("fps", 30.0))
        src_w = int(probe.get("width", self.cfg.output_width))
        src_h = int(probe.get("height", self.cfg.output_height))

        LOGGER.info(
            "Upscaling clip with Real-ESRGAN NCNN | model=%s | scale=%sx | tile=%s | threads=%s | gpu=%s",
            selected_model,
            selected_scale,
            selected_tile,
            selected_threads,
            selected_gpu,
        )

        # 1) Decode clip to PNG sequence and detached audio.
        decode_cmd = (
            ["nice", "-n", "10", "ffmpeg"]
            if Path("/usr/bin/nice").exists()
            else ["ffmpeg"]
        )
        decode_cmd.extend(
            [
                "-y",
                "-threads",
                "8",
                "-v",
                "warning",
                "-i",
                str(source),
                str(frames_in / "frame_%08d.png"),
            ]
        )
        run_cmd(decode_cmd, check=True)

        audio_cmd = (
            ["nice", "-n", "10", "ffmpeg"]
            if Path("/usr/bin/nice").exists()
            else ["ffmpeg"]
        )
        audio_cmd.extend(
            [
                "-y",
                "-threads",
                "8",
                "-v",
                "warning",
                "-i",
                str(source),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(audio_path),
            ]
        )
        run_cmd(audio_cmd, check=False)

        # 2) Real-ESRGAN over all frames.
        upscale_cmd = ["nice", "-n", "19"] if Path("/usr/bin/nice").exists() else []
        upscale_cmd.extend(
            [
                self.cfg.upscaling_binary,
                "-i",
                str(frames_in),
                "-o",
                str(frames_out),
                "-m",
                str(model_dir),
                "-n",
                selected_model,
                "-s",
                str(selected_scale),
                "-t",
                str(selected_tile),
                "-j",
                selected_threads,
                "-g",
                selected_gpu,
                "-f",
                "png",
            ]
        )
        run_cmd(upscale_cmd, check=True)

        # 3) Rebuild video and keep original timeline/audio.
        rebuild_cmd = (
            ["nice", "-n", "10", "ffmpeg"]
            if Path("/usr/bin/nice").exists()
            else ["ffmpeg"]
        )
        rebuild_cmd.extend(
            [
                "-y",
                "-threads",
                "8",
                "-v",
                "warning",
                "-framerate",
                f"{fps:.6f}",
                "-i",
                str(frames_out / "frame_%08d.png"),
            ]
        )
        has_audio = audio_path.exists() and audio_path.stat().st_size > 0
        if has_audio:
            rebuild_cmd.extend(["-i", str(audio_path)])

        if self.cfg.upscaling_downscale_to_original:
            rebuild_cmd.extend(
                [
                    "-vf",
                    f"scale={src_w}:{src_h}:flags=lanczos",
                ]
            )

        rebuild_cmd.extend(
            [
                "-c:v",
                self.cfg.ffmpeg_video_codec,
                "-preset",
                self.cfg.ffmpeg_nvenc_preset,
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                self.cfg.ffmpeg_video_bitrate,
                "-maxrate",
                self.cfg.ffmpeg_video_bitrate,
                "-bufsize",
                "20M",
            ]
        )

        if has_audio:
            rebuild_cmd.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:a", "copy"])
        else:
            rebuild_cmd.append("-an")

        rebuild_cmd.extend(["-shortest", str(reconstructed_path)])
        run_cmd(rebuild_cmd, check=True)

        reconstructed_path.replace(source)

        if not self.cfg.keep_intermediate_files:
            safe_rmtree(tmp_root)

        return UpscaleResult(
            output_path=source,
            scale=selected_scale,
            model=selected_model,
            backend="ncnn",
            face_enhance=False,
            face_model="none",
            tile_size=selected_tile,
            threads=selected_threads,
            gpu_id=selected_gpu,
        )

    def _upscale_with_python_face_enhance(
        self,
        source: Path,
        selected_model: str,
        selected_scale: int,
        selected_tile: int,
        selected_gpu: str,
        selected_face_model: str,
        selected_threads: str,
    ) -> UpscaleResult:

        script_path = self._resolve_python_script_path()
        if not script_path.exists() or not script_path.is_file():
            raise RuntimeError(
                "Real-ESRGAN Python script not found for face enhancement: "
                f"{script_path}. Set UPSCALING_PYTHON_SCRIPT to a valid path."
            )
        self._ensure_realesrgan_version_file(script_path)

        tmp_root = ensure_dir(self.cfg.temp_dir / "upscaling" / source.stem)
        output_dir = ensure_dir(tmp_root / "python_face")
        output_candidate = output_dir / f"{source.stem}_out.mp4"

        LOGGER.info(
            "Upscaling clip with Real-ESRGAN Python + face enhance | model=%s | scale=%sx | tile=%s | gpu=%s",
            selected_model,
            selected_scale,
            selected_tile,
            selected_gpu,
        )

        cmd = (
            ["nice", "-n", "10", sys.executable]
            if Path("/usr/bin/nice").exists()
            else [sys.executable]
        )
        cmd.extend(
            [
                str(script_path),
                "-i",
                str(source),
                "-o",
                str(output_dir),
                "-n",
                selected_model,
                "-s",
                str(selected_scale),
                "-t",
                str(selected_tile),
                "--face_enhance",
                "--face_model",
                selected_face_model,
                "--codeformer_fidelity",
                str(self.cfg.upscaling_codeformer_fidelity),
                "--suffix",
                "out",
                "--ffmpeg_bin",
                "ffmpeg",
            ]
        )
        if selected_gpu and selected_gpu.lower() != "auto":
            try:
                int(selected_gpu)
            except ValueError as exc:
                raise ValueError(
                    "UPSCALING_GPU_ID must be 'auto' or an integer GPU id for python face enhancement"
                ) from exc
            cmd.extend(["-g", selected_gpu])
        run_cmd(cmd, check=True)

        if not output_candidate.exists():
            raise RuntimeError(
                "Face-enhanced output was not generated as expected: "
                f"{output_candidate}"
            )

        if self.cfg.upscaling_downscale_to_original:
            probe = self._probe_video(source)
            src_w = int(probe.get("width", self.cfg.output_width))
            src_h = int(probe.get("height", self.cfg.output_height))
            downscaled = output_dir / f"{source.stem}_out_downscaled.mp4"
            ds_cmd = (
                ["nice", "-n", "10", "ffmpeg"]
                if Path("/usr/bin/nice").exists()
                else ["ffmpeg"]
            )
            ds_cmd.extend(
                [
                    "-y",
                    "-threads",
                    "8",
                    "-v",
                    "warning",
                    "-i",
                    str(output_candidate),
                    "-vf",
                    f"scale={src_w}:{src_h}:flags=lanczos",
                    "-c:v",
                    self.cfg.ffmpeg_video_codec,
                    "-preset",
                    self.cfg.ffmpeg_nvenc_preset,
                    "-pix_fmt",
                    "yuv420p",
                    "-b:v",
                    self.cfg.ffmpeg_video_bitrate,
                    "-maxrate",
                    self.cfg.ffmpeg_video_bitrate,
                    "-bufsize",
                    "20M",
                    "-c:a",
                    "copy",
                    str(downscaled),
                ]
            )
            run_cmd(ds_cmd, check=True)
            output_candidate = downscaled

        output_candidate.replace(source)

        if not self.cfg.keep_intermediate_files:
            safe_rmtree(tmp_root)

        return UpscaleResult(
            output_path=source,
            scale=selected_scale,
            model=selected_model,
            backend="python-face",
            face_enhance=True,
            face_model=selected_face_model,
            tile_size=selected_tile,
            threads=selected_threads,
            gpu_id=selected_gpu,
        )

    def _run_face_only_codeformer(
        self,
        source: Path,
        selected_gpu: str,
        selected_tile: int,
    ) -> UpscaleResult:
        """Apply CodeFormer face restoration only — no ESRGAN background upscaling.

        Keeps the original video resolution. Much faster than ESRGAN+CodeFormer.
        Suitable for real-time or near-real-time workflows.
        """
        script_path = self._resolve_python_script_path()
        if not script_path.exists() or not script_path.is_file():
            raise RuntimeError(f"Real-ESRGAN Python script not found: {script_path}")
        self._ensure_realesrgan_version_file(script_path)

        tmp_root = ensure_dir(self.cfg.temp_dir / "upscaling" / source.stem)
        output_dir = ensure_dir(tmp_root / "face_only")
        output_candidate = output_dir / f"{source.stem}_out.mp4"

        LOGGER.info(
            "Face-only CodeFormer on clip | fidelity=%.2f | source=%s",
            self.cfg.upscaling_codeformer_fidelity,
            source.name,
        )

        cmd = [
            sys.executable,
            str(script_path),
            "-i",
            str(source),
            "-o",
            str(output_dir),
            # model_name/outscale not used but required by argparse defaults
            "-n",
            "realesr-general-x4v3",
            "-s",
            "2",
            "-t",
            str(selected_tile),
            "--face_only_codeformer",
            "--codeformer_fidelity",
            str(self.cfg.upscaling_codeformer_fidelity),
            "--suffix",
            "out",
            "--ffmpeg_bin",
            "ffmpeg",
            "--fp32",  # avoids nvrtc JIT issues on non-standard CUDA installs
        ]

        # Fix: PyTorch 2.11+cu130 needs libnvrtc-builtins visible at runtime.
        # The lib ships with Ollama on this system but isn't in the default ldconfig.
        import os as _os

        extra_ld = "/usr/local/lib/ollama/mlx_cuda_v13"
        existing_ld = _os.environ.get("LD_LIBRARY_PATH", "")
        env_override = (
            {
                "LD_LIBRARY_PATH": f"{extra_ld}:{existing_ld}"
                if existing_ld
                else extra_ld
            }
            if extra_ld not in existing_ld
            else {}
        )

        run_cmd(cmd, check=True, env=env_override)

        if not output_candidate.exists():
            raise RuntimeError(
                f"Face-only CodeFormer output not generated: {output_candidate}"
            )

        output_candidate.replace(source)

        if not self.cfg.keep_intermediate_files:
            safe_rmtree(tmp_root)

        return UpscaleResult(
            output_path=source,
            scale=1,
            model="codeformer",
            backend="python-face-only",
            face_enhance=True,
            face_model="codeformer",
            tile_size=selected_tile,
            threads="1",
            gpu_id=selected_gpu,
        )

    def _resolve_model_dir(self) -> Path:
        if self.cfg.upscaling_model_dir is not None:
            return Path(self.cfg.upscaling_model_dir).expanduser().resolve()
        binary_path = Path(self.cfg.upscaling_binary).expanduser()
        if binary_path.is_absolute() or "/" in str(binary_path):
            return (binary_path.resolve().parent / "models").resolve()
        # Fallback for PATH binaries; Real-ESRGAN will still use its own default.
        return (self.cfg.project_root / "models").resolve()

    def _resolve_python_script_path(self) -> Path:
        configured = (self.cfg.upscaling_python_script or "").strip()
        if not configured:
            configured = "bin/Real-ESRGAN-0.3.0/inference_realesrgan_video.py"
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate.resolve()
        return (self.cfg.project_root / candidate).resolve()

    @staticmethod
    def _ensure_realesrgan_version_file(script_path: Path) -> None:
        root = script_path.parent
        version_py = root / "realesrgan" / "version.py"
        if version_py.exists():
            return
        version_txt = root / "VERSION"
        version = "0.3.0"
        if version_txt.exists():
            raw = (version_txt.read_text(encoding="utf-8") or "").strip()
            if raw:
                version = raw
        version_py.parent.mkdir(parents=True, exist_ok=True)
        version_py.write_text(
            "# GENERATED MINIMAL VERSION FILE FOR LOCAL SCRIPT EXECUTION\n"
            f"__version__ = '{version}'\n"
            "__gitsha__ = 'unknown'\n"
            "version_info = tuple(int(p) if p.isdigit() else p for p in __version__.split('.'))\n",
            encoding="utf-8",
        )

    @staticmethod
    def _validate_model_files(model_dir: Path, model_name: str, scale: int) -> None:
        if not model_dir.exists() or not model_dir.is_dir():
            raise RuntimeError(
                "Real-ESRGAN model directory not found: "
                f"{model_dir}. Place model files (.param/.bin) there or set UPSCALING_MODEL_DIR."
            )

        param_candidates = [
            model_dir / f"{model_name}-x{scale}.param",
            model_dir / f"{model_name}.param",
        ]
        bin_candidates = [
            model_dir / f"{model_name}-x{scale}.bin",
            model_dir / f"{model_name}.bin",
        ]
        param_found = next((p for p in param_candidates if p.exists()), None)
        bin_found = next((p for p in bin_candidates if p.exists()), None)

        if param_found is None or bin_found is None:
            expected = ", ".join(
                [
                    str(param_candidates[0]),
                    str(bin_candidates[0]),
                    str(param_candidates[1]),
                    str(bin_candidates[1]),
                ]
            )
            raise RuntimeError(
                "Real-ESRGAN model files missing/incomplete for selected model/scale. "
                f"Expected one matching pair in: {expected}"
            )

    @staticmethod
    def _probe_video(path: Path) -> dict:
        result = run_cmd(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        if not streams:
            return {"width": 1080, "height": 1920, "fps": 30.0}
        stream = streams[0]
        rate = str(stream.get("r_frame_rate", "30/1"))
        fps = 30.0
        if "/" in rate:
            num, den = rate.split("/", 1)
            try:
                den_f = float(den)
                if den_f != 0.0:
                    fps = float(num) / den_f
            except ValueError:
                fps = 30.0
        else:
            try:
                fps = float(rate)
            except ValueError:
                fps = 30.0
        return {
            "width": int(stream.get("width", 1080) or 1080),
            "height": int(stream.get("height", 1920) or 1920),
            "fps": max(1.0, fps),
        }
