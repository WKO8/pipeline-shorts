import argparse
import logging
from pathlib import Path
from src.config import load_config
from src.debug_visualizer import TrackingDebugVisualizer

def main():
    parser = argparse.ArgumentParser(description="Generate a debug video with face boxes and audiovisual speaker scores.")
    parser.add_argument("--video", type=str, required=True, help="Path to the source video.")
    parser.add_argument("--start", type=float, default=0.0, help="Start time in seconds.")
    parser.add_argument("--end", type=float, required=True, help="End time in seconds.")
    parser.add_argument("--output", type=str, default="debug_tracking.mp4", help="Output video path.")
    parser.add_argument("--env-file", type=str, default=".env", help="Path to .env file.")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    cfg = load_config(env_file=args.env_file, project_root=Path(__file__).resolve().parent)

    # Using default model name since yolo_model_name was not in AppConfig dataclass
    visualizer = TrackingDebugVisualizer(
        model_dir=cfg.data_dir / "models",
        imgsz=cfg.yolo_img_size,
    )

    visualizer.visualize_clip(
        video_path=Path(args.video),
        start_s=args.start,
        end_s=args.end,
        output_debug_path=Path(args.output),
        target_w=cfg.output_width,
        target_h=cfg.output_height
    )

if __name__ == "__main__":
    main()
