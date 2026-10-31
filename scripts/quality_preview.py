"""Render a short local preview; preserve existing clips and never call a curator."""
import argparse
import json
import logging
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.config import load_config
from src.models import ClipCandidate
from src.renderer import ShortsRenderer
from src.transcriber import transcribe_audio
from src.utils import run_cmd


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--video', required=True)
    p.add_argument('--start', type=float, required=True)
    p.add_argument('--duration', type=float, default=30)
    p.add_argument('--output-dir', default='data/quality-review')
    args=p.parse_args()
    cfg=load_config(project_root=Path(__file__).resolve().parents[1], env_file='.env')
    cfg.ensure_directories()
    output=Path(args.output_dir).resolve(); output.mkdir(parents=True, exist_ok=True)
    cfg.subtitles_dir=output/'subtitles'; cfg.temp_dir=output/'tmp'; cfg.ensure_directories()
    logging.basicConfig(level=logging.INFO)
    source=output/'source.mp4'; audio=output/'source.wav'
    started=time.monotonic()
    run_cmd(['ffmpeg','-y','-v','error','-ss',str(args.start),'-i',str(Path(args.video).resolve()),
             '-t',str(args.duration),'-c:v','libx264','-preset','fast','-crf','18','-c:a','aac',str(source)])
    run_cmd(['ffmpeg','-y','-v','error','-i',str(source),'-vn','-ac','1','-ar','16000',str(audio)])
    transcript=transcribe_audio(cfg,audio,output/'transcript.json')
    renderer=ShortsRenderer(cfg)
    path=renderer._new_tracker().track_clip(source,0,args.duration,debug_video_path=output/'tracking-debug.mp4')
    result=renderer.render_single_clip(source,transcript,ClipCandidate(title='Quality preview',start=0,end=args.duration),
                                       output_dir=output,slug='quality',index=1,tracking_override=path)
    report={'elapsed_seconds':round(time.monotonic()-started,2),'source_start':args.start,'duration':args.duration,
            'tracking':{'single_windows':path.active_speaker_single_windows,'wide_windows':path.fit_scene_windows},
            'result':result.to_dict()}
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
