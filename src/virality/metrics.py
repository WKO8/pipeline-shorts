"""
Audiovisual extraction for virality analysis:
- Duration (via ffprobe / OpenCV)
- Cut frequency & average cut interval (via PySceneDetect)
- Audio extraction and Whisper transcription with word timestamps
- Frame sampling focusing on the first 3 seconds (0.5s, 1.0s, 1.5s, 2.0s, 2.5s, 3.0s)
- OpenCV visual telemetry (luminance, contrast, face framing, motion, hook dynamics)
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

from ..config import AppConfig, load_config
from ..models import Transcript
from ..transcriber import WhisperTranscriber
from ..utils import extract_audio

LOGGER = logging.getLogger(__name__)


@dataclass
class FrameInfo:
    timestamp: float
    file_path: Path
    relative_url: str
    width: int
    height: int
    luminance: float
    contrast: float
    face_detected: bool
    face_count: int
    eye_line_ratio: Optional[float]
    is_hook_frame: bool  # True se estiver dentro dos primeiros 3 segundos


@dataclass
class ExtractedVideoData:
    video_path: Path
    duration: float
    cut_count: int
    cut_timestamps: List[float]
    avg_cut_interval: float
    transcript: Transcript
    transcript_with_timestamps: str
    frames: List[FrameInfo]
    visual_telemetry: dict[str, Any]


class VideoMetricsExtractor:
    """
    Extracts all technical parameters required for the Senior Viral Strategist.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.yunet_weights = Path(self.config.data_dir if hasattr(self.config, "data_dir") else "data") / "models" / "face" / "yunet.onnx"
        if not self.yunet_weights.exists():
            # Tenta caminho relativo ao projeto
            alt_path = Path(__file__).resolve().parent.parent.parent / "data" / "models" / "face" / "yunet.onnx"
            if alt_path.exists():
                self.yunet_weights = alt_path

    def extract_all(
        self,
        video_path: Path,
        output_frames_dir: Path,
        existing_transcript: Optional[Transcript] = None,
    ) -> ExtractedVideoData:
        """
        Executes full extraction pipeline on the target video.
        """
        video_path = Path(video_path).resolve()
        if not video_path.exists():
            raise FileNotFoundError(f"Arquivo de vídeo não encontrado: {video_path}")

        output_frames_dir.mkdir(parents=True, exist_ok=True)

        # 1. Duração e dimensões
        try:
            from ..vertical import probe
            p_info = probe(video_path)
            total_duration = float(p_info['duration'])
        except Exception as exc:
            LOGGER.warning("Falha ao obter duração via probe (%s). Usando OpenCV.", exc)
            cap = cv2.VideoCapture(str(video_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
            total_duration = float(total_frames / max(1.0, fps))
            cap.release()

        # 2. Detecção de cortes via PySceneDetect
        cut_timestamps, avg_cut_interval = self._detect_cuts(video_path, total_duration)

        # 3. Transcrição com timestamps
        if existing_transcript:
            transcript = existing_transcript
        else:
            transcript = self._transcribe(video_path)

        formatted_transcript = self._format_transcript_with_timestamps(transcript)

        # 4. Amostragem de frames focando nos primeiros 3 segundos
        frames, visual_telemetry = self._sample_and_analyze_frames(
            video_path=video_path,
            total_duration=total_duration,
            output_dir=output_frames_dir,
        )

        return ExtractedVideoData(
            video_path=video_path,
            duration=round(total_duration, 2),
            cut_count=len(cut_timestamps),
            cut_timestamps=cut_timestamps,
            avg_cut_interval=round(avg_cut_interval, 2),
            transcript=transcript,
            transcript_with_timestamps=formatted_transcript,
            frames=frames,
            visual_telemetry=visual_telemetry,
        )

    def _detect_cuts(self, video_path: Path, total_duration: float) -> Tuple[List[float], float]:
        """
        Detects scene cuts using PySceneDetect ContentDetector.
        Returns (cut_timestamps, avg_cut_interval).
        """
        try:
            from scenedetect import ContentDetector, detect
            scenes = detect(str(video_path), ContentDetector(threshold=27))
            cut_times: List[float] = []
            for start_tc, end_tc in scenes:
                cut_sec = round(start_tc.get_seconds(), 2)
                if cut_sec > 0.1:  # Ignora o corte zero inicial
                    cut_times.append(cut_sec)

            cut_count = len(cut_times) + 1  # Número de tomadas/cenas
            avg_interval = total_duration / max(1, cut_count)
            return cut_times, avg_interval
        except Exception as exc:
            LOGGER.warning("PySceneDetect falhou (%s). Utilizando estimativa por ffmpeg.", exc)
            return self._detect_cuts_ffmpeg(video_path, total_duration)

    def _detect_cuts_ffmpeg(self, video_path: Path, total_duration: float) -> Tuple[List[float], float]:
        """
        Fallback cut detector using ffmpeg select='gt(scene,0.3)'.
        """
        cmd = [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(video_path),
            "-filter_complex", "select='gt(scene,0.3)',metadata=print:file=-:key=lavfi.scene_score",
            "-f", "null", "-"
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
            cut_times = []
            for line in res.stderr.splitlines():
                if "pts_time:" in line:
                    parts = line.split("pts_time:")
                    if len(parts) > 1:
                        try:
                            val = float(parts[1].split()[0])
                            if val > 0.1:
                                cut_times.append(round(val, 2))
                        except ValueError:
                            pass
            cut_count = len(cut_times) + 1
            avg_interval = total_duration / max(1, cut_count)
            return cut_times, avg_interval
        except Exception as exc:
            LOGGER.error("Falha também no fallback do ffmpeg: %s", exc)
            return [], round(total_duration, 2)

    def _transcribe(self, video_path: Path) -> Transcript:
        """
        Extracts audio and transcribes via WhisperTranscriber.
        """
        audio_dir = video_path.parent / "audio_extracted"
        audio_dir.mkdir(parents=True, exist_ok=True)
        audio_path = audio_dir / f"{video_path.stem}.wav"

        extract_audio(str(video_path), str(audio_path))
        transcriber = WhisperTranscriber(self.config)
        return transcriber.transcribe(audio_path)

    def _format_transcript_with_timestamps(self, transcript: Transcript) -> str:
        """
        Formats transcript segments into clean timestamps representation.
        """
        if not transcript or not transcript.segments:
            return "(Nenhuma fala detectada no vídeo ou áudio instrumental/mudo)"

        lines = []
        for s in transcript.segments:
            text = s.text.strip()
            if text:
                lines.append(f"[{s.start:.2f}s - {s.end:.2f}s] {text}")
        return "\n".join(lines)

    def _sample_and_analyze_frames(
        self,
        video_path: Path,
        total_duration: float,
        output_dir: Path,
    ) -> Tuple[List[FrameInfo], dict[str, Any]]:
        """
        Samples frames:
        - First 3 seconds: [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        - Rest of video: sample frames at cut points or every few seconds
        Computes luminance, contrast, YuNet face detection and eye-line ratio.
        """
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir o vídeo com OpenCV: {video_path}")

        vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
        vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)

        # Configura YuNet se disponível
        face_detector = None
        if self.yunet_weights.exists():
            try:
                face_detector = cv2.FaceDetectorYN.create(
                    str(self.yunet_weights), "", (vw, vh), 0.7, 0.3, 5000
                )
            except Exception as e:
                LOGGER.warning("Não foi possível carregar YuNet: %s", e)

        # Definir pontos de amostragem
        # Primeiros 3 segundos têm alta densidade
        target_timestamps = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
        target_timestamps = [t for t in target_timestamps if t <= total_duration]

        # Adiciona pontos adicionais para o restante do vídeo (até 4 pontos extras)
        if total_duration > 4.0:
            step = (total_duration - 3.0) / 4.0
            for i in range(1, 4):
                extra_t = round(3.0 + i * step, 2)
                if extra_t < total_duration:
                    target_timestamps.append(extra_t)

        frames_info: List[FrameInfo] = []
        hook_luminances: List[float] = []
        hook_contrasts: List[float] = []
        hook_face_detections: List[bool] = []
        hook_eye_ratios: List[float] = []

        prev_gray = None
        motion_diffs: List[float] = []

        for t in target_timestamps:
            frame_filename = f"frame_{t:.2f}s.jpg"
            frame_path = output_dir / frame_filename
            frame = None

            # Extração via ffmpeg para suporte universal a todos os codecs (AV1, HEVC, H.264, VP9)
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t:.3f}",
                "-i", str(video_path),
                "-vframes", "1", "-q:v", "2",
                str(frame_path)
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                if frame_path.exists():
                    frame = cv2.imread(str(frame_path))
            except Exception:
                pass

            if frame is None:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if ok and frame is not None:
                    cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 90])

            if frame is None:
                continue

            # Métricas visuais
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            lum = float(np.mean(gray))
            contrast = float(np.std(gray))

            # Motion diff
            if prev_gray is not None and prev_gray.shape == gray.shape:
                diff = float(np.mean(cv2.absdiff(prev_gray, gray)))
                motion_diffs.append(diff)
            prev_gray = gray

            # Face & Eye-line
            face_detected = False
            face_count = 0
            eye_line_ratio = None
            if face_detector is not None:
                try:
                    _, faces = face_detector.detect(frame)
                    if faces is not None and len(faces) > 0:
                        face_detected = True
                        face_count = len(faces)
                        # Ordena pela maior área
                        primary_face = max(faces, key=lambda f: f[2] * f[3])
                        # f[4], f[5] são coordenadas (x,y) do olho direito, f[6], f[7] olho esquerdo
                        right_eye_y = float(primary_face[5])
                        left_eye_y = float(primary_face[7])
                        avg_eye_y = (right_eye_y + left_eye_y) / 2.0
                        eye_line_ratio = round(avg_eye_y / max(1.0, vh), 3)
                except Exception:
                    pass

            is_hook = t <= 3.0
            if is_hook:
                hook_luminances.append(lum)
                hook_contrasts.append(contrast)
                hook_face_detections.append(face_detected)
                if eye_line_ratio is not None:
                    hook_eye_ratios.append(eye_line_ratio)

            run_key = output_dir.parent.name if output_dir.name == "frames" else output_dir.name
            frames_info.append(
                FrameInfo(
                    timestamp=t,
                    file_path=frame_path,
                    relative_url=f"/api/virality/frames/{run_key}/{frame_filename}",
                    width=vw,
                    height=vh,
                    luminance=round(lum, 1),
                    contrast=round(contrast, 1),
                    face_detected=face_detected,
                    face_count=face_count,
                    eye_line_ratio=eye_line_ratio,
                    is_hook_frame=is_hook,
                )
            )

        cap.release()

        # Telemetria agregada dos 3 primeiros segundos
        avg_hook_lum = np.mean(hook_luminances) if hook_luminances else 128.0
        avg_hook_contrast = np.mean(hook_contrasts) if hook_contrasts else 50.0
        face_in_hook = any(hook_face_detections)
        avg_eye_ratio = np.mean(hook_eye_ratios) if hook_eye_ratios else None
        avg_motion = np.mean(motion_diffs[:3]) if motion_diffs else 0.0

        # Diagnóstico heurístico visual preliminar
        lighting_diagnostic = "Boa iluminação e contraste"
        if avg_hook_lum < 60.0:
            lighting_diagnostic = "Cena inicial muito escura/subexposta"
        elif avg_hook_lum > 210.0:
            lighting_diagnostic = "Cena inicial superexposta/estourada"
        elif avg_hook_contrast < 35.0:
            lighting_diagnostic = "Baixo contraste; visual opaco/apagado"

        framing_diagnostic = "Enquadramento adequado"
        if face_in_hook and avg_eye_ratio is not None:
            if avg_eye_ratio > 0.45:
                framing_diagnostic = "Rosto posicionado muito abaixo (corte de enquadramento ruim para Shorts)"
            elif avg_eye_ratio < 0.18:
                framing_diagnostic = "Rosto colado no topo (risco de corte de cabeça na barra de título)"

        visual_telemetry = {
            "resolution": f"{vw}x{vh}",
            "aspect_ratio": "9:16" if abs((vw / max(1, vh)) - (9 / 16)) < 0.1 else f"{vw}:{vh}",
            "hook_avg_luminance": round(float(avg_hook_lum), 1),
            "hook_avg_contrast": round(float(avg_hook_contrast), 1),
            "hook_face_detected": face_in_hook,
            "hook_avg_eye_line_ratio": round(float(avg_eye_ratio), 3) if avg_eye_ratio else None,
            "hook_avg_motion_diff": round(float(avg_motion), 2),
            "lighting_diagnostic": lighting_diagnostic,
            "framing_diagnostic": framing_diagnostic,
        }

        return frames_info, visual_telemetry
