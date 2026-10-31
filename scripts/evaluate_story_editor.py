"""Local editorial regression evaluation. No dotenv, credentials, paid APIs or deletions."""
import argparse
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import AppConfig
from src.models import Transcript,SourceSpan
from src.narrative import NarrativeCurator
from src.story_editor import StoryEditor,source_text
from src.utils import new_run_dir,write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='qwen3.5:4b')
    parser.add_argument('--split',choices=['all','development','holdout'],default='all')
    parser.add_argument('--baseline',action='store_true')
    args=parser.parse_args()
    folder=new_run_dir(ROOT/'data/validation/staged-editor');print(folder,flush=True)
    fixture=json.loads((ROOT/'tests/fixtures/editorial_cases.json').read_text())
    t=Transcript.from_dict(json.loads((ROOT/fixture['source']).read_text()))
    by_id={s.id:s for s in t.segments}
    spans=lambda pairs:[SourceSpan(by_id[a].start,by_id[b].end) for a,b in pairs]
    cfg=AppConfig(data_dir=folder,ollama_model=args.model,stage_cache_enabled=False,curation_cache_enabled=False,ollama_timeout_seconds=300)
    c=NarrativeCurator(cfg);c.audit=[];editor=StoryEditor(c);rows=[]
    try:
        for case in fixture['cases']:
            if args.split!='all' and case['split']!=args.split: continue
            parts=spans(case['parts']);original=spans(case.get('original_parts',case['parts']))
            start=time.perf_counter()
            try:
                result=editor.review(t,parts,case['title'],original)
                accepted=result['approved']
            except Exception as exc: result={'error':str(exc)};accepted=False
            row=dict(id=case['id'],split=case['split'],expected=case['acceptable'],accepted=accepted,
                     seconds=time.perf_counter()-start,result=result)
            if args.baseline:
                start=time.perf_counter()
                try:
                    old=c.review_story(case['title'],[dict(role='body',text=x) for x in source_text(t,parts)])
                    row['baseline_accepted']=old['decision']=='aprovar';row['baseline_result']=old
                except Exception as exc: row['baseline_accepted']=False;row['baseline_result']={'error':str(exc)}
                row['baseline_seconds']=time.perf_counter()-start
            rows.append(row);print(case['id'],row['expected'],row['accepted'],flush=True)
            write_json(folder/'cases.json',rows);write_json(folder/'audit.json',c.audit)
    finally: c.unload_model()
    summary=dict(model=args.model,decision_cache=False,cases=len(rows),
        false_accepts=sum(r['accepted'] and not r['expected'] for r in rows),
        false_rejects=sum(not r['accepted'] and r['expected'] for r in rows),
        total_seconds=sum(r['seconds'] for r in rows))
    if args.baseline:
        summary['baseline']=dict(false_accepts=sum(r['baseline_accepted'] and not r['expected'] for r in rows),
            false_rejects=sum(not r['baseline_accepted'] and r['expected'] for r in rows),seconds=sum(r['baseline_seconds'] for r in rows))
    write_json(folder/'summary.json',summary);print(summary,flush=True)

if __name__=='__main__': main()
