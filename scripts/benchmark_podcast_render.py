"""Render one measured editorial result and benchmark actual speaker tracking.

Argument: an existing data/benchmarks/real-podcast/run-* directory.
No dotenv, external API, deletion, or overwrite of a prior render run.
"""
from pathlib import Path
import json
import logging
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import AppConfig
from src.models import ClipCandidate, Transcript
from src.renderer import ShortsRenderer
from src.quality_review import write_quality_review
from src.utils import new_run_dir, write_json


def main():
    parent = Path(sys.argv[1]).resolve()
    if not parent.is_relative_to(ROOT/'data/benchmarks/real-podcast'):
        raise ValueError('Expected a benchmark directory inside this project')
    measured = json.loads((parent/'measurements.json').read_text())
    choices = [(window, clip) for window in measured['editorial'] for clip in window['reviewed']]
    if not choices:
        raise RuntimeError('No reviewed editorial result to render; do not substitute a fabricated success')
    window, data = min(choices, key=lambda pair: pair[1]['duration'])
    folder = new_run_dir(parent/'render')
    (folder/'models').symlink_to(ROOT/'data/models', target_is_directory=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(folder/'run.log'), logging.StreamHandler()])
    cfg = AppConfig(project_root=ROOT, data_dir=folder, temp_dir=folder/'tmp',
                    subtitles_dir=folder/'subtitles', clips_dir=folder/'clips')
    transcript_path = parent/f"transcript-{window['start']}.json"
    transcript = Transcript.from_dict(json.loads(transcript_path.read_text()))
    clip = ClipCandidate(**{k:v for k,v in data.items() if k!='duration'})
    source = Path(measured['source'])
    renderer = ShortsRenderer(cfg)
    report = dict(clip=data, timings={}, note='Existing transcript; no new word-alignment accuracy assessment.')

    def timed(name, fn):
        start = time.perf_counter()
        value = fn()
        report['timings'].setdefault(name,[]).append(time.perf_counter()-start)
        write_json(folder/'measurements.json',report)
        return value

    track = timed('tracking_cold',lambda:renderer._new_tracker().track_clip(source,clip.start,clip.end))
    for _ in range(3):
        cached = timed('tracking_cache',lambda:renderer._new_tracker().track_clip(source,clip.start,clip.end))
        assert track.to_dict()==cached.to_dict()
    rendered = timed('render_nvenc',lambda:renderer.render_single_clip(source,transcript,clip,folder,'real-podcast',
                      transcript_json_path=transcript_path,tracking_override=track))
    write_quality_review(folder,transcript,[clip],[rendered],source)
    probe = json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(rendered.output_path)]))
    video = next(s for s in probe['streams'] if s['codec_type']=='video')
    audio = next(s for s in probe['streams'] if s['codec_type']=='audio')
    report['output'] = str(rendered.output_path)
    report['validation'] = dict(width=video['width'],height=video['height'],fps=video['r_frame_rate'],
        video_seconds=float(video['duration']),audio_seconds=float(audio['duration']),requested_seconds=clip.duration)
    assert (video['width'],video['height'])==(1080,1920)
    assert abs(float(video['duration'])-clip.duration)<.1
    assert abs(float(video['duration'])-float(audio['duration']))<.1
    report['median_seconds']={k:statistics.median(v) for k,v in report['timings'].items()}
    write_json(folder/'measurements.json',report)
    for index, position in enumerate([.15,.5,.85]):
        subprocess.run(['ffmpeg','-v','error','-n','-ss',str(clip.duration*position),'-i',str(rendered.output_path),
                        '-frames:v','1','-vf','scale=360:640',str(folder/f'frame-{index}.png')],check=True)
    print('COMPLETE',folder,flush=True)


if __name__=='__main__':
    main()
