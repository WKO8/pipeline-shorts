import logging
from pathlib import Path
from src.tracking import SpeakerTracker

LOGGER = logging.getLogger(__name__)

class TrackingDebugVisualizer(SpeakerTracker):
    def visualize_clip(
        self,
        video_path: Path,
        start_s: float,
        end_s: float,
        output_debug_path: Path,
        target_w: int = 1080,
        target_h: int = 1920,
    ):
        self.track_clip(
            video_path=video_path,
            start_s=start_s,
            end_s=end_s,
            target_w=target_w,
            target_h=target_h,
            debug_video_path=output_debug_path
        )
