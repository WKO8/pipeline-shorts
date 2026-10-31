from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class WordTimestamp:
    """
    Represents a single spoken word with precise timing.
    """

    text: str
    start: float
    end: float
    probability: Optional[float] = None

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "probability": self.probability,
        }


@dataclass(slots=True)
class TranscriptSegment:
    """
    A chunk of transcript returned by ASR model (e.g., Whisper segment).
    """

    id: int
    start: float
    end: float
    text: str
    words: List[WordTimestamp] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "words": [w.to_dict() for w in self.words],
        }


@dataclass(slots=True)
class Transcript:
    """
    Full transcript with word-level timing, metadata and utilities.
    """

    language: str
    source_audio: Optional[Path] = None
    source_video: Optional[Path] = None
    segments: List[TranscriptSegment] = field(default_factory=list)
    timing_source: str = "unknown"

    @classmethod
    def from_dict(cls, data):
        return cls(
            language=data["language"], timing_source=data.get("timing_source", "unknown"),
            source_audio=Path(data["source_audio"]) if data.get("source_audio") else None,
            source_video=Path(data["source_video"]) if data.get("source_video") else None,
            segments=[TranscriptSegment(
                id=item["id"], start=float(item["start"]), end=float(item["end"]),
                text=item["text"], words=[WordTimestamp(**word) for word in item.get("words", [])]
            ) for item in data["segments"]])

    @property
    def words(self) -> List[WordTimestamp]:
        all_words: List[WordTimestamp] = []
        for seg in self.segments:
            all_words.extend(seg.words)
        return all_words

    @property
    def full_text(self) -> str:
        return " ".join(
            seg.text.strip() for seg in self.segments if seg.text.strip()
        ).strip()

    @property
    def start(self) -> float:
        if not self.segments:
            return 0.0
        return min(seg.start for seg in self.segments)

    @property
    def end(self) -> float:
        if not self.segments:
            return 0.0
        return max(seg.end for seg in self.segments)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "language": self.language,
            "timing_source": self.timing_source,
            "source_audio": str(self.source_audio) if self.source_audio else None,
            "source_video": str(self.source_video) if self.source_video else None,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "full_text": self.full_text,
            "segments": [s.to_dict() for s in self.segments],
        }


@dataclass(slots=True)
class ContextualOverlay:
    """
    Time-scoped overlay asset (image/GIF/video) anchored near speaker chest area.
    """

    asset_path: str
    start: float
    end: float
    y_offset: int = 400
    scale: float = 1.0
    max_width: int = 420

    def to_dict(self) -> Dict[str, Any]:
        return {
            "asset_path": self.asset_path,
            "start": self.start,
            "end": self.end,
            "y_offset": self.y_offset,
            "scale": self.scale,
            "max_width": self.max_width,
        }


@dataclass(slots=True)
class SourceSpan:
    """One source interval, in playback order; hook may intentionally repeat."""
    start: float
    end: float
    role: str = "body"

    def to_dict(self):
        return {"start": self.start, "end": self.end, "role": self.role}


@dataclass(slots=True)
class ClipCandidate:
    """
    Candidate clip suggested by curation logic / LLM.
    """

    title: str
    start: float
    end: float
    reason: str = ""
    score: float = 0.0
    tags: List[str] = field(default_factory=list)
    layout_mode: str = "single"
    b_roll_overlay: Optional[str] = None
    contextual_overlays: List[ContextualOverlay] = field(default_factory=list)
    source_spans: List[SourceSpan] = field(default_factory=list)

    @property
    def source_ranges(self):
        return [(p.start, p.end) for p in self.source_spans] or [(self.start, self.end)]

    @property
    def duration(self) -> float:
        if self.source_spans:
            return sum(max(0.0, p.end - p.start) for p in self.source_spans)
        return max(0.0, self.end - self.start)

    def clamp(self, video_duration: float) -> "ClipCandidate":
        start = min(max(0.0, self.start), max(0.0, video_duration))
        end = min(max(start, self.end), max(0.0, video_duration))
        return ClipCandidate(
            title=self.title,
            start=start,
            end=end,
            reason=self.reason,
            score=self.score,
            tags=list(self.tags),
            layout_mode=self.layout_mode,
            b_roll_overlay=self.b_roll_overlay,
            contextual_overlays=list(self.contextual_overlays),
            source_spans=[SourceSpan(p.start, p.end, p.role) for p in self.source_spans],
        )

    def intersects(self, other: "ClipCandidate") -> bool:
        return not (self.end <= other.start or other.end <= self.start)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "duration": self.duration,
            "reason": self.reason,
            "score": self.score,
            "tags": self.tags,
            "layout_mode": self.layout_mode,
            "b_roll_overlay": self.b_roll_overlay,
            "contextual_overlays": [overlay.to_dict() for overlay in self.contextual_overlays],
            "source_spans": [part.to_dict() for part in self.source_spans],
        }


@dataclass(slots=True)
class PipelineArtifacts:
    """
    Key paths produced during pipeline execution.
    """

    working_dir: Path
    raw_video: Optional[Path] = None
    raw_audio: Optional[Path] = None
    transcript_json: Optional[Path] = None
    clips_json: Optional[Path] = None
    rendered_clips: List[Path] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "working_dir": str(self.working_dir),
            "raw_video": str(self.raw_video) if self.raw_video else None,
            "raw_audio": str(self.raw_audio) if self.raw_audio else None,
            "transcript_json": str(self.transcript_json)
            if self.transcript_json
            else None,
            "clips_json": str(self.clips_json) if self.clips_json else None,
            "rendered_clips": [str(p) for p in self.rendered_clips],
        }

@dataclass(slots=True)
class TrackingPoint:
    t: float
    x: float
    y: float
    w: Optional[float] = None
    h: Optional[float] = None
    z: Optional[float] = None
    fx: Optional[float] = None
    fy: Optional[float] = None
    cut: bool = False


@dataclass(slots=True)
class TrackingPath:
    source_width: int
    source_height: int
    crop_width: int
    crop_height: int
    points: list[TrackingPoint]
    secondary_points: list[TrackingPoint] = field(default_factory=list)
    duo_left_points: list[TrackingPoint] = field(default_factory=list)
    duo_right_points: list[TrackingPoint] = field(default_factory=list)
    fit_scene_mode: bool = False
    fit_scene_windows: list[tuple[float, float]] = field(default_factory=list)
    face_confidence_avg: float = 0.0
    face_detect_ratio: float = 0.0
    multi_face_ratio: float = 0.0
    active_speaker_multi_face_ratio: float = 0.0
    active_speaker_multi_face_windows: list[tuple[float, float]] = field(default_factory=list)
    active_speaker_single_windows: list[tuple[float, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source_width": self.source_width,
            "source_height": self.source_height,
            "crop_width": self.crop_width,
            "crop_height": self.crop_height,
            "fit_scene_mode": self.fit_scene_mode,
            "fit_scene_windows": [
                {"start": start, "end": end}
                for start, end in self.fit_scene_windows
            ],
            "face_confidence_avg": float(self.face_confidence_avg or 0.0),
            "face_detect_ratio": float(self.face_detect_ratio or 0.0),
            "points": [
                {
                    "t": float(p.t),
                    "cut": p.cut,
                    "x": float(p.x),
                    "y": float(p.y),
                    "w": float(p.w) if p.w is not None else None,
                    "h": float(p.h) if p.h is not None else None,
                    "z": float(p.z) if p.z is not None else None,
                    "fx": float(p.fx) if p.fx is not None else None,
                    "fy": float(p.fy) if p.fy is not None else None,
                }
                for p in self.points
            ],
            "secondary_points": [
                {
                    "t": float(p.t),
                    "cut": p.cut,
                    "x": float(p.x),
                    "y": float(p.y),
                    "w": float(p.w) if p.w is not None else None,
                    "h": float(p.h) if p.h is not None else None,
                    "z": float(p.z) if p.z is not None else None,
                    "fx": float(p.fx) if p.fx is not None else None,
                    "fy": float(p.fy) if p.fy is not None else None,
                }
                for p in self.secondary_points
            ],
            "duo_left_points": [
                {
                    "t": float(p.t),
                    "cut": p.cut,
                    "x": float(p.x),
                    "y": float(p.y),
                    "w": float(p.w) if p.w is not None else None,
                    "h": float(p.h) if p.h is not None else None,
                    "z": float(p.z) if p.z is not None else None,
                    "fx": float(p.fx) if p.fx is not None else None,
                    "fy": float(p.fy) if p.fy is not None else None,
                }
                for p in self.duo_left_points
            ],
            "duo_right_points": [
                {
                    "t": float(p.t),
                    "cut": p.cut,
                    "x": float(p.x),
                    "y": float(p.y),
                    "w": float(p.w) if p.w is not None else None,
                    "h": float(p.h) if p.h is not None else None,
                    "z": float(p.z) if p.z is not None else None,
                    "fx": float(p.fx) if p.fx is not None else None,
                    "fy": float(p.fy) if p.fy is not None else None,
                }
                for p in self.duo_right_points
            ],
            "multi_face_ratio": float(self.multi_face_ratio or 0.0),
            "active_speaker_multi_face_ratio": float(self.active_speaker_multi_face_ratio or 0.0),
            "active_speaker_multi_face_windows": [
                {"start": start, "end": end}
                for start, end in self.active_speaker_multi_face_windows
            ],
            "active_speaker_single_windows": [
                {"start": start, "end": end}
                for start, end in self.active_speaker_single_windows
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TrackingPath":
        def _points(items) -> list[TrackingPoint]:
            out: list[TrackingPoint] = []
            for item in items or []:
                if not isinstance(item, dict):
                    continue
                out.append(
                    TrackingPoint(
                        t=float(item.get("t", 0.0)),
                        cut=bool(item.get("cut", False)),
                        x=float(item.get("x", 0.0)),
                        y=float(item.get("y", 0.0)),
                        w=float(item.get("w", 0.0)) if item.get("w") is not None else None,
                        h=float(item.get("h", 0.0)) if item.get("h") is not None else None,
                        z=float(item.get("z", 0.0)) if item.get("z") is not None else None,
                        fx=float(item.get("fx", 0.0)) if item.get("fx") is not None else None,
                        fy=float(item.get("fy", 0.0)) if item.get("fy") is not None else None,
                    )
                )
            return out

        return cls(
            source_width=int(payload.get("source_width", 0)),
            source_height=int(payload.get("source_height", 0)),
            crop_width=int(payload.get("crop_width", 0)),
            crop_height=int(payload.get("crop_height", 0)),
            points=_points(payload.get("points", [])),
            secondary_points=_points(payload.get("secondary_points", [])),
            duo_left_points=_points(payload.get("duo_left_points", [])),
            duo_right_points=_points(payload.get("duo_right_points", [])),
            fit_scene_mode=bool(payload.get("fit_scene_mode", False)),
            fit_scene_windows=[
                (float(w["start"]), float(w["end"]))
                for w in payload.get("fit_scene_windows", [])
            ],
            face_confidence_avg=float(payload.get("face_confidence_avg", 0.0)),
            face_detect_ratio=float(payload.get("face_detect_ratio", 0.0)),
            multi_face_ratio=float(payload.get("multi_face_ratio", 0.0)),
            active_speaker_multi_face_ratio=float(
                payload.get("active_speaker_multi_face_ratio", 0.0)
            ),
            active_speaker_multi_face_windows=[
                (float(w["start"]), float(w["end"]))
                for w in payload.get("active_speaker_multi_face_windows", [])
            ],
            active_speaker_single_windows=[
                (float(w["start"]), float(w["end"]))
                for w in payload.get("active_speaker_single_windows", [])
            ],
        )
