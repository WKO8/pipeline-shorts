"""Offline/local benchmark on existing podcast; no dotenv, deletion or paid API.

Run from project root. Every run creates its own cache and evidence directory.
Editorial comparison is a review-on/off ablation, not a historical git baseline.
"""
from pathlib import Path
import copy
import json
import logging
import os
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
from src.config import AppConfig
from src.curator import OllamaCurator
from src.models import Transcript, ClipCandidate
from src.transcriber import WhisperTranscriber
from src.quality_review import write_quality_review
from src.utils import new_run_dir, write_json


class CapturingCurator(OllamaCurator):
    def _review_candidates(self, candidates, transcript):
        self.pool = copy.deepcopy(candidates)
        return candidates  # baseline; identical pool then independently reviewed


def main():
    folder = new_run_dir(ROOT / 'data/benchmarks/real-podcast')
    print(folder, flush=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s',
                        handlers=[logging.FileHandler(folder/'run.log'), logging.StreamHandler()])
    (folder/'models').symlink_to(ROOT/'data/models', target_is_directory=True)
    source = ROOT/'data/downloads/davy-jones.mp4'
    cfg = AppConfig(project_root=ROOT, data_dir=folder, temp_dir=folder/'tmp',
        subtitles_dir=folder/'subtitles', clips_dir=folder/'clips',
        ollama_clips_count=3, ollama_timeout_seconds=600)
    # Use the locally present pinned snapshot; never download a replacement.
    cfg.whisper_alignment_model = str(ROOT/'data/models/alignment/models--jonatasgrosman--wav2vec2-large-xlsr-53-portuguese/snapshots/634ac655299bcdc46c83bc01da9bab52d2987e4f')
    results = dict(source=str(source), windows_seconds=[[1800,2160],[3600,3960],[5400,5760]],
                   comparison='configuration ablation; not historical code baseline', timings={}, editorial=[])

    def timed(name, fn):
        start = time.perf_counter()
        value = fn()
        seconds = time.perf_counter()-start
        results['timings'].setdefault(name, []).append(seconds)
        write_json(folder/'measurements.json', results)
        logging.info('BENCHMARK %s %.3fs', name, seconds)
        return value

    curator = CapturingCurator(cfg)
    curator.unload_model()
    audio = folder/'sample-1800-2160.wav'
    timed('audio_extract', lambda: subprocess.run(['ffmpeg','-v','error','-n','-ss','1800',
          '-i',str(source),'-t','360','-vn','-ac','1','-ar','16000',str(audio)], check=True))
    asr = WhisperTranscriber(cfg)
    raw = timed('asr_cold', lambda: asr.transcribe(audio, defer_alignment=True))
    write_json(folder/'asr-raw.json', raw.to_dict())
    asr.unload_model()
    for _ in range(3):
        cached = timed('asr_cache', lambda: asr.transcribe(audio, defer_alignment=True))
        assert cached.to_dict() == raw.to_dict()

    existing = json.loads((ROOT/'data/transcripts/davy-jones-igor-3k-free.transcript.json').read_text())
    first_clips = []
    for start, end in results['windows_seconds']:
        # Existing transcript in all editorial arms: isolates reviewer effect from ASR changes.
        window = dict(existing, segments=[s for s in existing['segments'] if s['start'] >= start and s['end'] <= end])
        transcript = Transcript.from_dict(window)
        cfg.editorial_review_enabled = False
        curator.pool = []
        baseline = timed(f'curation_{start}', lambda: curator.curate(transcript))
        pool = getattr(curator, 'pool', [])
        cfg.editorial_review_enabled = True
        reviewed = timed(f'review_{start}', lambda: OllamaCurator._review_candidates(curator, copy.deepcopy(pool), transcript))
        final = curator._post_process_candidates(reviewed, transcript.end, 3, transcript)
        final = curator._enrich_clip_metadata(curator._refine_for_hook_and_story(final, transcript), transcript)
        entry = dict(start=start, end=end, raw_candidates=[c.to_dict() for c in pool],
                     baseline=[c.to_dict() for c in baseline.clips], reviewed=[c.to_dict() for c in final])
        results['editorial'].append(entry)
        write_json(folder/f'transcript-{start}.json', transcript.to_dict())
        write_json(folder/f'editorial-{start}.json', entry)
        (folder/f'raw-{start}.txt').write_text(baseline.raw_response)
        write_quality_review(folder/f'before-{start}', transcript, baseline.clips, source_video=source)
        write_quality_review(folder/f'after-{start}', transcript, final, source_video=source)
        cfg.editorial_review_enabled = False
        for _ in range(3):
            timed(f'curation_cache_{start}', lambda: curator.curate(transcript))
        cfg.editorial_review_enabled = True
        for _ in range(3):
            timed(f'review_cache_{start}', lambda: OllamaCurator._review_candidates(curator, copy.deepcopy(pool), transcript))
        if start == 1800:
            first_clips = final or baseline.clips
    curator.unload_model()
    if first_clips:
        selected = [ClipCandidate(c.title, max(0,c.start-1800), min(360,c.end-1800), reason=c.reason, score=c.score) for c in first_clips]
    else:
        selected = [ClipCandidate('Fixed timing-only probe; not editorial output',120,180)]
    results['alignment_probe'] = [c.to_dict() for c in selected]
    cfg.stage_cache_enabled = False
    full = timed('alignment_full', lambda: asr.align_selected(Transcript.from_dict(raw.to_dict()), audio))
    write_json(folder/'aligned-full.json', full.to_dict())
    cfg.stage_cache_enabled = True
    partial = timed('alignment_selective', lambda: asr.align_selected(Transcript.from_dict(raw.to_dict()), audio, selected))
    write_json(folder/'aligned-selective.json', partial.to_dict())
    for _ in range(3):
        timed('alignment_cache', lambda: asr.align_selected(Transcript.from_dict(raw.to_dict()), audio, selected))
    ids = {s.id for s in raw.segments if any(s.end>c.start and s.start<c.end for c in selected)}
    results['alignment'] = dict(total_segments=len(raw.segments), selected_segments=len(ids),
        full_source=full.timing_source, selective_source=partial.timing_source,
        selected_words_equal=all(a.to_dict()==b.to_dict() for a,b in zip(full.segments,partial.segments) if a.id in ids))
    results['median_seconds'] = {k:statistics.median(v) for k,v in results['timings'].items()}
    results['external_api_cost_usd'] = 0
    write_json(folder/'measurements.json', results)
    print('COMPLETE', folder, flush=True)


if __name__ == '__main__':
    main()
