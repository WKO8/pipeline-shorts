"""Repeat only editorial review on saved candidates after a reviewer change."""
from pathlib import Path
import json
import logging
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import AppConfig
from src.curator import OllamaCurator
from src.models import ClipCandidate, Transcript
from src.quality_review import write_quality_review
from src.utils import new_run_dir, write_json


def main():
    parent = Path(sys.argv[1]).resolve()
    if not parent.is_relative_to(ROOT/'data/benchmarks/real-podcast'):
        raise ValueError('Benchmark must be inside the project')
    original = json.loads((parent/'measurements.json').read_text())
    folder = new_run_dir(parent/'editor-recheck')
    logging.basicConfig(level=logging.INFO,handlers=[logging.FileHandler(folder/'run.log'),logging.StreamHandler()])
    cfg = AppConfig(data_dir=folder,ollama_clips_count=3,ollama_timeout_seconds=600)
    curator = OllamaCurator(cfg)
    result = dict(source=original['source'],editorial=[],timings={},baseline_run=str(parent))
    for window in original['editorial']:
        transcript = Transcript.from_dict(json.loads((parent/f"transcript-{window['start']}.json").read_text()))
        write_json(folder/f"transcript-{window['start']}.json",transcript.to_dict())
        pool = [ClipCandidate(**{k:v for k,v in item.items() if k!='duration'}) for item in window['raw_candidates']]
        start = time.perf_counter()
        reviewed = curator._review_candidates(pool,transcript)
        result['timings'][str(window['start'])] = time.perf_counter()-start
        final = curator._post_process_candidates(reviewed,transcript.end,3,transcript)
        final = curator._enrich_clip_metadata(curator._refine_for_hook_and_story(final,transcript),transcript)
        entry = dict(window,previous_reviewed=window['reviewed'],reviewed=[c.to_dict() for c in final],
                     accepted_before_floor=[c.to_dict() for c in reviewed])
        result['editorial'].append(entry)
        write_quality_review(folder/f"after-{window['start']}",transcript,final,source_video=Path(original['source']))
        write_json(folder/'measurements.json',result)
    curator.unload_model()
    print('COMPLETE',folder,flush=True)


if __name__=='__main__':
    main()
