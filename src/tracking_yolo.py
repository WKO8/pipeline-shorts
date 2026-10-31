import os
import gc
import logging
import re
import math
import subprocess
import urllib.request
from collections import deque
from pathlib import Path
from typing import Optional, Generator
import threading

_MODEL_LOCK = threading.Lock()

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Set CPU threads dynamically based on available hardware cores (up to 8 threads)
# ---------------------------------------------------------------------------
_OPTIMAL_CPU_THREADS = max(1, min(8, (os.cpu_count() or 4) // 2))
cv2.setNumThreads(_OPTIMAL_CPU_THREADS)
try:
    import torch as _torch_early
    _torch_early.set_num_threads(_OPTIMAL_CPU_THREADS)
    _torch_early.set_num_interop_threads(max(1, _OPTIMAL_CPU_THREADS // 2))
except Exception:
    pass

from scenedetect import ContentDetector, detect

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None

try:
    import torch
except ImportError:
    torch = None

from src.models import TrackingPath, TrackingPoint
from src.utils import run_cmd

LOGGER = logging.getLogger(__name__)

def _parse_fraction_rate(expr: str) -> float | None:
    expr = (expr or "").strip()
    if not expr or expr == "0/0": return None
    m = re.match(r"^(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)$", expr)
    if m:
        num, den = float(m.group(1)), float(m.group(2))
        return num / den if den != 0.0 else None
    try: return float(expr)
    except: return None

def _probe_video_fps_ffprobe(video_path: str) -> float | None:
    try:
        result = run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=0", video_path], check=True)
        avg, rfr = None, None
        for line in (result.stdout or "").splitlines():
            if line.startswith("avg_frame_rate="): avg = _parse_fraction_rate(line.split("=")[1])
            elif line.startswith("r_frame_rate="): rfr = _parse_fraction_rate(line.split("=")[1])
        for c in (avg, rfr):
            if c is not None and 5.0 <= c <= 120.0: return c
    except: pass
def _iter_clip_frames(
    video_path: str | Path,
    start_frame: int,
    end_frame: int,
    fps: float,
    src_w: int,
    src_h: int,
    chunk_size: int = 16,
) -> Generator[tuple[list[np.ndarray], list[int]], None, None]:
    cap = cv2.VideoCapture(str(video_path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    ok, test_fr = cap.read()
    if ok and test_fr is not None:
        f_idx = start_frame
        chunk = [test_fr]
        indices = [f_idx]
        f_idx += 1
        while f_idx < end_frame:
            while len(chunk) < chunk_size and f_idx < end_frame:
                ok, fr = cap.read()
                if not ok or fr is None:
                    break
                chunk.append(fr)
                indices.append(f_idx)
                f_idx += 1
            if chunk:
                yield chunk, indices
                chunk, indices = [], []
            else:
                break
        cap.release()
        return

    cap.release()
    LOGGER.info(
        "OpenCV decoding unavailable or failed for %s. Decoding via FFmpeg rawvideo pipe.",
        Path(video_path).name,
    )
    start_s = float(start_frame) / fps
    dur_s = float(end_frame - start_frame) / fps
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{start_s:.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{max(0.1, dur_s):.3f}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "bgr24",
        "pipe:1",
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    frame_bytes = src_w * src_h * 3
    f_idx = start_frame
    chunk, indices = [], []
    while f_idx < end_frame:
        raw = p.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            break
        fr = np.frombuffer(raw, dtype=np.uint8).reshape((src_h, src_w, 3))
        chunk.append(fr)
        indices.append(f_idx)
        f_idx += 1
        if len(chunk) >= chunk_size:
            yield chunk, indices
            chunk, indices = [], []
    if chunk:
        yield chunk, indices
    p.kill()


class YoloPersonTracker:
    """
    Optimized Face-Lock Tracker using YOLO Keypoints.
    Ensures face (nose/eyes) is ALWAYS horizontally centered and at desired height.
    """
    def __init__(self, model_name="yolov8n-face.pt", ease_alpha=0.25, max_pan_speed_px_per_s=2000.0, chunk_size_frames=16, imgsz=640, missed_detection_hold_frames=20, min_fit_scene_seconds=0.12, eye_line_ratio=0.30):
        self.model_name = model_name
        self.ease_alpha = ease_alpha
        self.max_pan_speed_px_per_s = max_pan_speed_px_per_s
        self.chunk_size_frames = max(4, chunk_size_frames)
        self.imgsz = imgsz
        self.missed_detection_hold_frames = max(1, missed_detection_hold_frames)
        self.min_fit_scene_seconds = max(0.0, float(min_fit_scene_seconds))
        self.eye_line_ratio = float(np.clip(eye_line_ratio, 0.10, 0.50))
        self._model = None
        self._use_half = self._cuda_supports_half()

    @staticmethod
    def _cuda_supports_half():
        if torch is None: return False
        try: return torch.cuda.is_available()
        except: return False
    def _load_model(self):
        with _MODEL_LOCK:
            if self._model is not None: return
            model_path = Path(self.model_name)
            if not model_path.exists():
                for cand in [Path("bin") / self.model_name, Path("bin/yolov8n-face.pt"), Path("yolov8n-face.pt")]:
                    if cand.exists():
                        self.model_name = str(cand)
                        model_path = cand
                        break
            if not model_path.exists():
                url = "https://github.com/derronqi/yolov8-face/releases/download/v1.0/yolov8n-face.pt"
                LOGGER.info("Face model %s not found. Downloading...", self.model_name)
                try: urllib.request.urlretrieve(url, self.model_name)
                except Exception as e:
                    LOGGER.error("Failed to download face model: %s", e)
                    if not model_path.exists(): self.model_name = "yolov8n.pt"
            self._model = YOLO(self.model_name)

    def _unload_model(self):
        with _MODEL_LOCK:
            self._model = None
            gc.collect()
            if torch and torch.cuda.is_available(): torch.cuda.empty_cache()

    def _detect_scenes(self, video_path, start_s, end_s, fps):
        try:
            from scenedetect import SceneManager, open_video
            from scenedetect.detectors import ContentDetector as _CD
            video = open_video(video_path, framerate=fps)
            video.seek(start_s)
            mgr = SceneManager()
            mgr.add_detector(_CD(threshold=15.0))
            mgr.detect_scenes(video, end_time=end_s)
            scenes = mgr.get_scene_list()
        except: 
            try: scenes = detect(video_path, ContentDetector(threshold=15.0))
            except: scenes = []
        s_fr, e_fr = max(0, int(round(start_s*fps))), int(round(end_s*fps))
        clip_scenes = []
        for s in scenes:
            ss, se = s[0].get_frames(), s[1].get_frames()
            ist, ied = max(s_fr, ss), min(e_fr, se)
            if ist < ied: clip_scenes.append((ist, ied))
        if not clip_scenes: return [(s_fr, e_fr)]
        filled, cur = [], s_fr
        for ist, ied in clip_scenes:
            if ist > cur: filled.append((cur, ist))
            filled.append((ist, ied))
            cur = ied
        if cur < e_fr: filled.append((cur, e_fr))
        return filled

    def track_clip(
        self,
        video_path,
        start_s,
        end_s,
        target_w=1080,
        target_h=1920,
        debug_video_path=None,
        speaker_windows=None,
    ) -> TrackingPath:
        video_path = Path(video_path)
        cap = cv2.VideoCapture(str(video_path))
        fps = (
            _probe_video_fps_ffprobe(str(video_path))
            or cap.get(cv2.CAP_PROP_FPS)
            or 30.0
        )
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
        cap.release()

        scenes = self._detect_scenes(video_path, start_s, end_s, fps)
        start_frame = max(0, int(round(start_s * fps)))
        end_frame = max(start_frame + 1, int(round(end_s * fps)))

        self._load_model()
        LOGGER.info(
            "Face Tracking Pass: Collecting detections for %s (%d to %d frames)",
            video_path.name,
            start_frame,
            end_frame,
        )

        detections_by_frame = {}
        for chunk, indices in _iter_clip_frames(
            video_path=video_path,
            start_frame=start_frame,
            end_frame=end_frame,
            fps=fps,
            src_w=src_w,
            src_h=src_h,
            chunk_size=self.chunk_size_frames,
        ):
            infer_kwargs = {
                "verbose": False,
                "imgsz": self.imgsz,
                "stream": True,
                "conf": 0.35,
            }
            if torch and torch.cuda.is_available():
                infer_kwargs["device"] = 0
            if torch and hasattr(torch, "inference_mode"):
                with torch.inference_mode():
                    results = list(self._model(chunk, **infer_kwargs))
            else:
                results = list(self._model(chunk, **infer_kwargs))

            for i, r in enumerate(results):
                boxes = r.boxes
                kpts = getattr(r, "keypoints", None)
                dets = []
                if len(boxes) > 0:
                    xyxy = boxes.xyxy.cpu().numpy()
                    xywh = boxes.xywh.cpu().numpy()
                    kp_data = kpts.data.cpu().numpy() if kpts is not None else None
                    conf_data = (
                        boxes.conf.cpu().numpy()
                        if hasattr(boxes, "conf") and boxes.conf is not None
                        else None
                    )
                    for j in range(len(boxes)):
                        cf = float(conf_data[j]) if conf_data is not None else 0.7
                        dets.append(
                            {
                                "xyxy": xyxy[j],
                                "xywh": xywh[j],
                                "kpts": kp_data[j] if kp_data is not None else None,
                                "conf": cf,
                            }
                        )
                detections_by_frame[indices[i]] = dets

        self._unload_model()

        tar_ar = target_w / target_h
        base_crop_h = src_h
        base_crop_w = int(round(base_crop_h * tar_ar))
        if base_crop_w > src_w:
            base_crop_w = src_w
            base_crop_h = int(round(base_crop_w / tar_ar))

        # -------------------------------------------------------------------
        # 1. Filter out background screens, wall posters, and tiny faces
        # -------------------------------------------------------------------
        cleaned_detections: dict[int, list[dict]] = {}
        for f, dets in detections_by_frame.items():
            valid = []
            for d in dets:
                area = float(d["xywh"][2] * d["xywh"][3])
                cy = float(d["xywh"][1])
                # Background TV screens or wall posters are tiny relative to foreground humans
                if cy < 0.28 * src_h and area < 12000:
                    continue
                if area < 4000:
                    continue
                valid.append(d)
            cleaned_detections[f] = valid

        # -------------------------------------------------------------------
        # 2. Track left and right subjects for Duo Split (Top / Bottom)
        # -------------------------------------------------------------------
        half_h = target_h // 2
        # In a duo layout (vertical 9:16 split into two stacked boxes),
        # each box has aspect ratio target_w / (target_h // 2) = 1.125.
        # To get a natural, medium close-up framing on each host (cutting out
        # background TV screens and table clutter between them), crop ~58% of source height:
        duo_crop_h = max(2, min(src_h, int(round(src_h * 0.58))))
        duo_crop_w = max(2, min(src_w, int(round(duo_crop_h * (target_w / float(half_h))))))
        if duo_crop_w % 2:
            duo_crop_w += 1
        if duo_crop_h % 2:
            duo_crop_h += 1
        duo_max_x = max(0, src_w - duo_crop_w)
        duo_max_y = max(0, src_h - duo_crop_h)

        left_anchors = [
            d["xywh"][0]
            for dets in cleaned_detections.values()
            for d in dets
            if d["xywh"][0] < 0.45 * src_w
        ]
        right_anchors = [
            d["xywh"][0]
            for dets in cleaned_detections.values()
            for d in dets
            if d["xywh"][0] > 0.55 * src_w
        ]
        default_lx = float(np.median(left_anchors)) if left_anchors else 0.20 * src_w
        default_rx = float(np.median(right_anchors)) if right_anchors else 0.80 * src_w

        left_y_anchors = [
            d["xywh"][1]
            for dets in cleaned_detections.values()
            for d in dets
            if d["xywh"][0] < 0.45 * src_w
        ]
        right_y_anchors = [
            d["xywh"][1]
            for dets in cleaned_detections.values()
            for d in dets
            if d["xywh"][0] > 0.55 * src_w
        ]
        default_ly = float(np.median(left_y_anchors)) if left_y_anchors else 0.52 * src_h
        default_ry = float(np.median(right_y_anchors)) if right_y_anchors else 0.52 * src_h

        cur_lx, cur_ly = default_lx, default_ly
        cur_rx, cur_ry = default_rx, default_ry

        duo_left_points: list[TrackingPoint] = []
        duo_right_points: list[TrackingPoint] = []
        points: list[TrackingPoint] = []
        missed_flags: list[bool] = []

        total_frames = end_frame - start_frame
        spk_windows = speaker_windows or []

        for i in range(total_frames):
            f_idx = start_frame + i
            t_sec = float(i) / fps
            dets = cleaned_detections.get(f_idx, [])

            left_dets = [d for d in dets if d["xywh"][0] < 0.45 * src_w]
            right_dets = [d for d in dets if d["xywh"][0] > 0.55 * src_w]

            if left_dets:
                best_l = max(left_dets, key=lambda d: d["xywh"][2] * d["xywh"][3])
                cur_lx = 0.85 * cur_lx + 0.15 * float(best_l["xywh"][0])
                cur_ly = 0.85 * cur_ly + 0.15 * float(best_l["xywh"][1])
            else:
                cur_lx = 0.98 * cur_lx + 0.02 * default_lx
                cur_ly = 0.98 * cur_ly + 0.02 * default_ly

            if right_dets:
                best_r = max(right_dets, key=lambda d: d["xywh"][2] * d["xywh"][3])
                cur_rx = 0.85 * cur_rx + 0.15 * float(best_r["xywh"][0])
                cur_ry = 0.85 * cur_ry + 0.15 * float(best_r["xywh"][1])
            else:
                cur_rx = 0.98 * cur_rx + 0.02 * default_rx
                cur_ry = 0.98 * cur_ry + 0.02 * default_ry

            # Duo left crop (Top slot in vertical short)
            crop_lx = float(np.clip(cur_lx - 0.5 * duo_crop_w, 0, duo_max_x))
            crop_ly = float(np.clip(cur_ly - self.eye_line_ratio * duo_crop_h, 0, duo_max_y))
            duo_left_points.append(
                TrackingPoint(
                    t=t_sec,
                    x=crop_lx,
                    y=crop_ly,
                    w=duo_crop_w,
                    h=duo_crop_h,
                    fx=0.5,
                    fy=self.eye_line_ratio,
                )
            )

            # Duo right crop (Bottom slot in vertical short)
            crop_rx = float(np.clip(cur_rx - 0.5 * duo_crop_w, 0, duo_max_x))
            crop_ry = float(np.clip(cur_ry - self.eye_line_ratio * duo_crop_h, 0, duo_max_y))
            duo_right_points.append(
                TrackingPoint(
                    t=t_sec,
                    x=crop_rx,
                    y=crop_ry,
                    w=duo_crop_w,
                    h=duo_crop_h,
                    fx=0.5,
                    fy=self.eye_line_ratio,
                )
            )

            # Single speaker crop tracking
            is_active_right = True
            if spk_windows:
                is_active_right = any(
                    w[0] <= (start_s + t_sec) <= w[1] for w in spk_windows
                )

            target_fx = cur_rx if is_active_right else cur_lx
            target_fy = cur_ry if is_active_right else cur_ly

            max_single_x = max(0, src_w - base_crop_w)
            max_single_y = max(0, src_h - base_crop_h)
            crop_sx = float(np.clip(target_fx - 0.5 * base_crop_w, 0, max_single_x))
            crop_sy = float(np.clip(target_fy - self.eye_line_ratio * base_crop_h, 0, max_single_y))
            points.append(
                TrackingPoint(
                    t=t_sec,
                    x=crop_sx,
                    y=crop_sy,
                    w=base_crop_w,
                    h=base_crop_h,
                    fx=0.5,
                    fy=self.eye_line_ratio,
                )
            )
            missed_flags.append(len(dets) == 0)

        def _f2w(fl, fr):
            ws, si = [], None
            for idx, m in enumerate(fl):
                if m and si is None:
                    si = idx
                elif not m and si is not None:
                    ws.append((si / fr, idx / fr))
                    si = None
            return [w for w in ws if (w[1] - w[0]) >= self.min_fit_scene_seconds]

        return TrackingPath(
            source_width=src_w,
            source_height=src_h,
            crop_width=int(points[0].w if points else base_crop_w),
            crop_height=int(points[0].h if points else base_crop_h),
            points=points,
            secondary_points=duo_right_points,
            duo_left_points=duo_left_points,
            duo_right_points=duo_right_points,
            fit_scene_mode=False,
            fit_scene_windows=_f2w(missed_flags, fps),
        )

    def _render_debug(self, video_path, start_frame, end_frame, fps, src_w, src_h, detections, states, out_path):
        cap = cv2.VideoCapture(str(video_path))
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        out = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*'mp4v'), fps, (src_w, src_h))
        for i, (rx, ry, rw, rh) in enumerate(states):
            ok, frame = cap.read()
            if not ok: break
            dets = detections.get(start_frame + i, [])
            for d in dets: cv2.rectangle(frame, (int(d["xyxy"][0]), int(d["xyxy"][1])), (int(d["xyxy"][2]), int(d["xyxy"][3])), (0, 0, 255), 2)
            cv2.rectangle(frame, (int(rx), int(ry)), (int(rx + rw), int(ry + rh)), (255, 0, 0), 3)
            tx, ty = rx + 0.5 * rw, ry + self.eye_line_ratio * rh
            cv2.circle(frame, (int(tx), int(ty)), 10, (0, 255, 0), -1)
            out.write(frame)
        cap.release(); out.release()
