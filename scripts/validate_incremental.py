"""Create a synthetic NVENC/ASS/review smoke test, preserving every run.

No config files, credentials, downloaded models or user media are loaded.
Run from the project root: .venv/bin/python scripts/validate_incremental.py
"""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import AppConfig
from src.models import Transcript, TranscriptSegment, WordTimestamp, ClipCandidate
from src.renderer import ShortsRenderer
from src.quality_review import write_quality_review
from src.utils import new_run_dir, write_json


def main():
    folder = new_run_dir(ROOT / 'data/validation/incremental')
    source = folder / 'synthetic-source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-f', 'lavfi', '-i',
                    'color=c=0x23384f:s=1280x720:r=30:d=5', '-f', 'lavfi', '-i',
                    'sine=frequency=440:sample_rate=48000:duration=5',
                    '-af', 'volume=0.02', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', '-shortest', str(source)], check=True)
    cfg = AppConfig(project_root=ROOT, data_dir=folder, temp_dir=folder/'tmp',
                    subtitles_dir=folder/'subtitles', clips_dir=folder/'clips',
                    enable_face_tracking=False, captions_animated=False,
                    captions_font_size=64, clip_min_seconds=1, clip_max_seconds=10)
    tokens = ['Uma', 'história', 'precisa', 'de', 'uma', 'conclusão.']
    words = [WordTimestamp(word, .3+i*.65, .3+i*.65+.55) for i, word in enumerate(tokens)]
    transcript = Transcript('pt', timing_source='synthetic-fixture', source_video=source,
                            segments=[TranscriptSegment(0, 0, 5, ' '.join(tokens), words)])
    transcript_path = folder/'transcript.json'
    write_json(transcript_path, transcript.to_dict())
    clip = ClipCandidate('Validação sintética — sem ASR ou APIs', 0, 5, score=0,
                         reason='Tempos artificiais usados exclusivamente para testar exportação e legendas.')
    renderer = ShortsRenderer(cfg)
    result = renderer.render_single_clip(source, transcript, clip, folder, 'synthetic',
                                         transcript_json_path=transcript_path)
    report = write_quality_review(folder, transcript, [clip], [result], source)
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                                               '-of', 'json', str(result.output_path)]))
    video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    audio = next(s for s in probe['streams'] if s['codec_type'] == 'audio')
    assert (video['width'], video['height']) == (1080, 1920)
    assert abs(float(video['duration'])-5) < .05
    assert abs(float(video['duration'])-float(audio['duration'])) < .05
    image = folder/'frame.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-n', '-ss', '1.2', '-i', str(result.output_path),
                    '-frames:v', '1', str(image)], check=True)
    write_json(folder/'validation.json', dict(video_duration=video['duration'],
               audio_duration=audio['duration'], width=1080, height=1920, report=str(report)))
    print(json.dumps(dict(report=str(report), video=str(result.output_path), frame=str(image)), indent=2))


if __name__ == '__main__':
    main()
