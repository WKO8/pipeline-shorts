"""Face tracking with audiovisual speaker selection and conservative shot fallback."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import tempfile

import cv2
import numpy as np

from .active_speaker import ActiveSpeakerModel, choose_speakers, face_tensor, stabilize_shots
from .models import TrackingPath, TrackingPoint
from .utils import run_cmd

LOGGER = logging.getLogger(__name__)


def boolean_windows(flags, fps=25):
    windows, start = [], None
    for idx, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = idx
        elif not flag and start is not None:
            windows.append((start / fps, idx / fps))
            start = None
    return windows


def box_iou(a, b):
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    intersection = max(0, min(ax+aw, bx+bw)-max(ax, bx)) * max(0, min(ay+ah, by+bh)-max(ay, by))
    return intersection / max(1, aw*ah + bw*bh - intersection)


class SpeakerTracker:
    def __init__(self, model_dir=Path("data/models"), ease_alpha=0.25,
                 max_pan_speed_px_per_s=900, eye_line_ratio=0.3,
                 speaker_threshold=0.7, speaker_margin=0.2, speaker_hold=0.4,
                 device="auto", cache_enabled=True, **_legacy):
        import os
        cv2.setNumThreads(max(1, min(8, (os.cpu_count() or 4)//2)))
        self.model_dir = Path(model_dir)
        self.ease_alpha = ease_alpha
        self.max_pan_speed = max_pan_speed_px_per_s
        self.eye_line_ratio = eye_line_ratio
        self.threshold = speaker_threshold
        self.margin = speaker_margin
        self.hold = speaker_hold
        self.device = device
        self.cache_enabled = cache_enabled

    def track_clip(self, video_path, start_s, end_s, target_w=1080, target_h=1920,
                   debug_video_path=None, speaker_windows=None):
        if end_s <= start_s:
            raise ValueError("Clip end must be after start")
        from .stage_cache import StageCache, digest, file_digest, package_versions
        cache = StageCache(self.model_dir.parent, self.cache_enabled and debug_video_path is None)
        cache_key = digest(dict(version=2, source=file_digest(Path(video_path)),
            start=start_s, end=end_s, width=target_w, height=target_h,
            ease=self.ease_alpha, pan=self.max_pan_speed, eye=self.eye_line_ratio,
            threshold=self.threshold, margin=self.margin, hold=self.hold, device=self.device,
            weights={str(p.relative_to(self.model_dir)): file_digest(p) for p in
                     (self.model_dir / "face/yunet.onnx", self.model_dir / "asd/lr_asd.model") if p.exists()},
            packages=package_versions("opencv-python", "torch", "scenedetect")))
        cached = cache.get("tracking", cache_key)
        if cached is not None:
            return TrackingPath.from_dict(cached)
        info = json.loads(run_cmd(["ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", str(video_path)]).stdout)["streams"][0]
        src_w, src_h = info["width"], info["height"]
        weights = self.model_dir / "face/yunet.onnx"
        if not weights.exists():
            raise FileNotFoundError("Face weights missing: run python scripts/setup_quality_models.py")
        from contextlib import nullcontext
        temp_root = self.model_dir.parent / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with nullcontext(tempfile.mkdtemp(prefix="shorts-tracking-", dir=temp_root)) as temp:
            analysis = Path(temp) / "analysis.mp4"
            # Shared, explicit timebase handles 29.97 fps and VFR sources. Only the
            # selected clip is decoded, never the entire podcast into RAM.
            run_cmd(["ffmpeg", "-v", "error", "-y", "-ss", str(start_s), "-i", str(video_path),
                     "-t", str(end_s-start_s), "-an", "-vf", "fps=25,scale='min(960,iw)':-2",
                     "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", str(analysis)])
            audio_result = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start_s), "-i", str(video_path),
                "-t", str(end_s-start_s), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                capture_output=True, check=False)
            audio = np.frombuffer(audio_result.stdout, dtype=np.int16)
            if audio_result.returncode:
                LOGGER.warning("Audio unavailable for ASD; preserving multi-person shots")
                audio = np.empty(0, dtype=np.int16)
            from scenedetect import detect, ContentDetector
            scenes = {a.get_frames() for a, _ in detect(str(analysis), ContentDetector(threshold=27))}
            scenes.add(0)
            cap = cv2.VideoCapture(str(analysis))
            aw, ah = int(cap.get(3)), int(cap.get(4))
            detector = cv2.FaceDetectorYN.create(str(weights), "", (aw, ah), 0.75, 0.3, 5000)
            detections, tracks, previous, next_id = [], {}, {}, 0
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    idx = len(detections)
                    if idx in scenes:
                        previous = {}
                    _, faces = detector.detect(frame)
                    faces = [] if faces is None else sorted(faces, key=lambda row: -row[2]*row[3])[:6]
                    current, available = {}, set(previous)
                    for row in faces:
                        if min(row[2:4]) < 16:
                            continue
                        tid = max(available, key=lambda key: box_iou(previous[key], row)) if available else None
                        if tid is None or box_iou(previous[tid], row) < 0.25:
                            tid = next_id
                            next_id += 1
                        else:
                            available.remove(tid)
                        current[tid] = row
                        tracks.setdefault(tid, []).append((idx, face_tensor(frame, row)))
                    detections.append(current)
                    previous = current
            finally:
                cap.release()
            scores = {}
            asd_weights = self.model_dir / "asd/lr_asd.model"
            if len(audio) and asd_weights.exists():
                scorer = ActiveSpeakerModel(asd_weights, self.device)
                try:
                    # Single-face shots don't require inference: follow source editing.
                    needed = {tid for dets in detections for tid, row in dets.items()
                              if len(dets) > 1 or float(row[2])/aw < 0.18}
                    for tid, samples in tracks.items():
                        if tid not in needed or len(samples) < 5:
                            continue
                        first, last = samples[0][0], samples[-1][0]
                        chunk_audio = audio[first*640:(last+1)*640]
                        if not len(chunk_audio):
                            continue
                        prob = scorer.score(np.stack([face for _, face in samples]), chunk_audio)
                        # 200 ms smoothing suppresses isolated score spikes.
                        prob = np.convolve(np.pad(prob, (2, 2), mode="edge"), np.ones(5)/5, mode="valid")
                        scores.update({(idx, tid): float(p) for (idx, _), p in zip(samples, prob)})
                finally:
                    del scorer
                    from .transcriber import release_gpu_memory
                    release_gpu_memory()
            else:
                LOGGER.warning("ASD unavailable; run scripts/setup_quality_models.py. Multi-face shots stay wide.")
            del tracks
            selected = choose_speakers(detections, scores, scenes, threshold=self.threshold,
                                      margin=self.margin, hold_seconds=self.hold, frame_width=aw)
            selected = stabilize_shots(selected, scenes)
            crop_h = min(int(src_h * 0.85), int(src_w * target_h / target_w)) // 2 * 2
            crop_w = min(src_w, int(crop_h * target_w / target_h)) // 2 * 2
            points, single, last_id = [], [], None
            x, y = (src_w-crop_w)/2, (src_h-crop_h)/2
            for idx, (dets, tid) in enumerate(zip(detections, selected)):
                if tid is not None and tid in dets:
                    face = dets[tid]
                    fx = (face[0]+face[2]/2) * src_w/aw
                    fy = float((face[5]+face[7])/2) * src_h/ah  # eyes, not body center
                    tx = float(np.clip(fx-crop_w/2, 0, src_w-crop_w))
                    ty = float(np.clip(fy-self.eye_line_ratio*crop_h, 0, src_h-crop_h))
                    if idx in scenes or tid != last_id:
                        x, y = tx, ty  # hard cut at shot/speaker changes
                    else:
                        x += float(np.clip((tx-x)*self.ease_alpha, -self.max_pan_speed/25, self.max_pan_speed/25))
                        y += float(np.clip((ty-y)*self.ease_alpha, -self.max_pan_speed/25, self.max_pan_speed/25))
                    single.append(True)
                else:
                    single.append(False)
                points.append(TrackingPoint(t=idx/25, x=x, y=y, w=crop_w, h=crop_h, cut=(idx in scenes or tid != last_id)))
                last_id = tid
            duration = min(end_s-start_s, len(points)/25)
            windows = [(a, min(b, duration)) for a, b in boolean_windows(single)]
            if debug_video_path:
                self._debug(analysis, debug_video_path, detections, selected, scores, points, src_w/aw, src_h/ah)
            LOGGER.info("ASD tracking: %.1fs, %d tracks, %.0f%% focused / %.0f%% wide",
                        duration, next_id, 100*sum(single)/max(1,len(single)), 100*(1-sum(single)/max(1,len(single))))
            result = TrackingPath(source_width=src_w, source_height=src_h, crop_width=crop_w,
                crop_height=crop_h, points=points, fit_scene_mode=not all(single),
                fit_scene_windows=boolean_windows([not s for s in single]),
                active_speaker_single_windows=windows,
                face_detect_ratio=sum(bool(d) for d in detections)/max(1,len(detections)),
                multi_face_ratio=sum(len(d)>1 for d in detections)/max(1,len(detections)))
            cache.put("tracking", cache_key, result.to_dict())
            return result

    @staticmethod
    def _debug(video, output, detections, selected, scores, points, sx, sy):
        cap = cv2.VideoCapture(str(video))
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), 25, (int(cap.get(3)), int(cap.get(4))))
        try:
            for idx, dets in enumerate(detections):
                ok, frame = cap.read()
                if not ok:
                    break
                for tid, row in dets.items():
                    x,y,w,h = map(int, row[:4])
                    color = (0,255,0) if tid == selected[idx] else (0,180,255)
                    cv2.rectangle(frame, (x,y), (x+w,y+h), color, 2)
                    cv2.putText(frame, f"{tid}: {scores.get((idx,tid),0):.2f}", (x,max(15,y-6)), cv2.FONT_HERSHEY_SIMPLEX, .5, color, 1)
                p = points[idx]
                if selected[idx] is not None:
                    cv2.rectangle(frame, (int(p.x/sx),int(p.y/sy)), (int((p.x+p.w)/sx),int((p.y+p.h)/sy)), (255,100,0), 2)
                else:
                    cv2.putText(frame, "WIDE / uncertain", (10,25), cv2.FONT_HERSHEY_SIMPLEX, .7, (255,255,255), 2)
                writer.write(frame)
        finally:
            cap.release()
            writer.release()
