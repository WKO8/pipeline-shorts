"""Bounded-memory LR-ASD inference (25 fps faces, 16 kHz mono audio).

Architecture: Junhua-Liao/LR-ASD, IJCV 2025, MIT; see vendor/lr_asd.
Scores are model probabilities, not calibrated certainty about speaker identity.
"""
from pathlib import Path

import numpy as np


class ActiveSpeakerModel:
    def __init__(self, weights: Path, device: str = "auto"):
        import torch
        from .vendor.lr_asd.Model import ASD_Model

        self.torch = torch
        self.device = "cuda" if device == "auto" and torch.cuda.is_available() else device
        if self.device == "auto":
            self.device = "cpu"
        state = torch.load(weights, map_location="cpu", weights_only=True)
        self.model = ASD_Model()
        self.model.load_state_dict({k[6:]: v for k, v in state.items() if k.startswith("model.")})
        self.head = torch.nn.Linear(128, 2)
        self.head.load_state_dict({k[10:]: v for k, v in state.items() if k.startswith("lossAV.FC.")})
        self.model.to(self.device).eval()
        self.head.to(self.device).eval()

    def score(self, faces: np.ndarray, audio: np.ndarray) -> np.ndarray:
        """Score one contiguous face track, using 4 s context and 1 s overlap."""
        import python_speech_features

        count = len(faces)
        if count < 5:
            return np.zeros(count, dtype=np.float32)
        # Exactly 4 MFCC rows per frame; preserve the official int16 amplitude.
        features = python_speech_features.mfcc(audio, 16000, numcep=13,
                                               winlen=0.025, winstep=0.010)
        missing = max(0, count * 4 - len(features))
        features = np.pad(features, ((0, missing), (0, 0)), mode="edge")[:count * 4]
        scores = np.zeros(count, dtype=np.float32)
        weights = np.zeros(count, dtype=np.float32)
        torch = self.torch
        with torch.inference_mode():
            for start in range(0, count, 50):
                end = min(count, start + 100)
                if end - start < 5:
                    break
                a = torch.as_tensor(features[start*4:end*4], dtype=torch.float32,
                                    device=self.device)[None]
                v = torch.as_tensor(faces[start:end], dtype=torch.float32,
                                    device=self.device)[None]
                embedding = self.model.forward_audio_visual_backend(
                    self.model.forward_audio_frontend(a), self.model.forward_visual_frontend(v))
                prob = self.head(embedding).softmax(-1)[:, 1].cpu().numpy()
                # Lower weight at chunk edges where bidirectional context is short.
                w = np.minimum(1.0, (np.minimum(np.arange(len(prob)), np.arange(len(prob))[::-1]) + 1) / 15)
                scores[start:end] += prob * w
                weights[start:end] += w
        return scores / np.maximum(weights, 1e-6)


def face_tensor(frame: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Replicate the reference demo's expanded crop and central 112x112 view."""
    import cv2

    x, y, w, h = box[:4]
    cx, cy, half = x + w / 2, y + h / 2, max(w, h) / 2
    pad = int(np.ceil(half * 1.8)) + 2
    padded = cv2.copyMakeBorder(frame, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    cx, cy = cx + pad, cy + pad
    crop = padded[int(cy-half):int(cy+half*1.8), int(cx-half*1.4):int(cx+half*1.4)]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (224, 224))[56:168, 56:168].copy()


def choose_speakers(detections, scores, scenes, fps=25, threshold=0.7, margin=0.2,
                    hold_seconds=0.4, frame_width=None):
    """No left/right assumptions. Ambiguity returns None (preserve full shot)."""
    selected, current, pending, pending_frames = [], None, None, 0
    required = max(1, round(hold_seconds * fps))
    for frame_idx, dets in enumerate(detections):
        if frame_idx in scenes:
            current, pending, pending_frames = None, None, 0
        if len(dets) == 1:
            tid = next(iter(dets))
            # One detection is not necessarily a close-up: the speaker may be
            # occluded while a small listener's face remains visible.
            closeup = frame_width is None or float(dets[tid][2]) / frame_width >= 0.18
            candidate = tid if closeup or scores.get((frame_idx, tid), 0) >= threshold else None
        elif dets:
            ranked = sorted(((scores.get((frame_idx, tid), 0.0), tid) for tid in dets), reverse=True)
            candidate = ranked[0][1] if ranked[0][0] >= threshold and ranked[0][0] - ranked[1][0] >= margin else None
        else:
            candidate = None
        if candidate == current:
            pending, pending_frames = None, 0
        elif candidate != pending:
            pending, pending_frames = candidate, 1
        else:
            pending_frames += 1
        if current not in dets or frame_idx in scenes or pending_frames >= required:
            current = candidate
            pending, pending_frames = None, 0
        selected.append(current)
    return selected


def stabilize_shots(selected, scenes, fps=25, minimum_seconds=0.8):
    """Remove short focus flashes and fill tiny gaps only for the same identity."""
    result = list(selected)
    boundaries = sorted({0, len(result), *scenes})
    for a,b in zip(boundaries, boundaries[1:]):
        i=a
        while i<b:
            end=i+1
            while end<b and result[end]==result[i]:
                end+=1
            if result[i] is None and i>a and end<b and end-i<=round(.4*fps) and result[i-1]==result[end]:
                result[i:end]=[result[end]]*(end-i)
            i=end
        i=a
        while i<b:
            end=i+1
            while end<b and result[end]==result[i]:
                end+=1
            if result[i] is not None and end-i<round(minimum_seconds*fps):
                result[i:end]=[None]*(end-i)
            i=end
    return result
