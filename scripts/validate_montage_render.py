"""Real-media integration fixture with MANUAL edit decisions, not an AI quality score.

Uses only project media/models, creates a fresh folder, and never reads dotenv.
"""
import json
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.config import AppConfig
from src.models import ClipCandidate, SourceSpan, Transcript
from src.renderer import ShortsRenderer
from src.quality_review import refine_render_clips, write_quality_review
from src.utils import new_run_dir, write_json


def main():
    folder = new_run_dir(ROOT / 'data/validation/montage-render')
    print(folder, flush=True)
    (folder / 'models').symlink_to(ROOT / 'data/models', target_is_directory=True)
    logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(folder / 'run.log'), logging.StreamHandler()])
    cfg = AppConfig(project_root=ROOT, data_dir=folder, temp_dir=folder/'tmp',
        subtitles_dir=folder/'subtitles', clips_dir=folder/'clips', silence_jump_cuts_enabled=True)
    transcript = Transcript.from_dict(json.loads((ROOT/'data/transcripts/davy-jones-igor-3k-free.transcript.json').read_text()))
    source = ROOT/'data/downloads/davy-jones.mp4'
    clip = ClipCandidate('Por que Pragmata funcionou — montagem manual de validação', 3756.8, 3788.0,
        reason='Fixture manual: repete explicação como preview e omite uma repetição; não mede seleção da IA.',
        score=9, source_spans=[SourceSpan(3773.1,3779.4,'hook'),SourceSpan(3756.8,3783.5),SourceSpan(3785.58,3788.0)])
    clip = refine_render_clips([clip], transcript, cfg)[0]
    write_json(folder/'manual-plan.json', clip.to_dict())
    start = time.perf_counter()
    result = ShortsRenderer(cfg).render_single_clip(source, transcript, clip, folder, 'manual-montage')
    write_quality_review(folder, transcript, [clip], [result], source)
    write_json(folder/'measurements.json', dict(render_seconds=time.perf_counter()-start,
        planned_seconds=clip.duration, output=str(result.output_path), automatic_editorial_validation=False))
    print('COMPLETE', folder, flush=True)


if __name__ == '__main__':
    main()
