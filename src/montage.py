"""Assemble source passages before the unchanged tracking/caption renderer."""
from dataclasses import replace
import math
from pathlib import Path

from .models import Transcript, TranscriptSegment, WordTimestamp
from .utils import new_run_dir, run_cmd, write_json


def remap_transcript(transcript, spans, fps):
    output, manifest, cursor = [], [], 0.0
    for part in spans:
        if not math.isfinite(part.start+part.end) or part.start<0 or part.end<=part.start:
            raise ValueError('Invalid montage interval')
        duration = math.ceil((part.end-part.start)*fps-1e-8)/fps
        records = []
        for source in transcript.segments:
            if source.end<=part.start or source.start>=part.end:
                continue
            start, end = max(part.start,source.start), min(part.end,source.end)
            words = [WordTimestamp(w.text,max(part.start,w.start)-part.start+cursor,
                min(part.end,w.end)-part.start+cursor,w.probability) for w in source.words
                if w.end>part.start and w.start<part.end and w.end>w.start]
            # Whole source segments are planned; word-boundary refinement can add
            # a tiny neighbor fragment. Never caption its unplayed whole sentence.
            text = source.text if start<=source.start and end>=source.end else ' '.join(w.text for w in words)
            if not text.strip():
                continue
            new_id = len(output)
            output.append(TranscriptSegment(new_id,start-part.start+cursor,end-part.start+cursor,text,words))
            records.append(dict(output_id=new_id,source_id=source.id))
        manifest.append(dict(source_start=part.start,source_end=part.end,role=part.role,
                             output_start=cursor,output_end=cursor+duration,segments=records))
        cursor += duration
    return Transcript(transcript.language,segments=output,timing_source=transcript.timing_source),manifest,cursor


def prepare_montage(source, transcript, clip, folder, config, *, encoder='h264_nvenc'):
    """One bounded sequential encode per passage; copy-concat, no files deleted.

    The CPU encoder parameter is for portable integration tests. Production uses
    NVENC; visual styling still happens only in the existing renderer.
    """
    folder = new_run_dir(Path(folder)/'edits')
    mapped, timeline, duration = remap_transcript(transcript,clip.source_spans,config.output_fps)
    if duration > config.clip_max_seconds + len(clip.source_spans)/config.output_fps:
        raise ValueError('Montage exceeds duration limit')
    paths = []
    for index, row in enumerate(timeline):
        target = folder/f'part-{index:02d}.mkv'
        d = row['output_end']-row['output_start']
        source_duration = row['source_end']-row['source_start']
        cmd = ['ffmpeg','-v','error','-n','-threads','4','-ss',f"{row['source_start']:.6f}",
               '-t',f'{source_duration:.6f}','-i',str(source),'-map','0:v:0','-map','0:a:0',
               '-vf',f'fps={config.output_fps},tpad=stop_mode=clone:stop_duration=0.1,trim=duration={d:.6f},setpts=PTS-STARTPTS,format=yuv420p',
               '-af',f'aresample=48000,apad,atrim=duration={d:.6f},asetpts=PTS-STARTPTS,afade=t=in:d=0.005,afade=t=out:st={max(0,d-.005):.6f}:d=0.005',
               '-c:v',encoder]
        cmd += ['-preset','p6','-cq','16','-b:v','0'] if encoder=='h264_nvenc' else ['-preset','ultrafast','-crf','18']
        cmd += ['-c:a','pcm_s16le','-t',f'{d:.6f}',str(target)]
        run_cmd(cmd,check=True)
        paths.append(target)
    listing = folder/'parts.ffconcat'
    # All paths are generated local filenames; resolve relative to the list.
    listing.write_text('ffconcat version 1.0\n'+''.join(f"file '{p.name}'\n" for p in paths))
    video = folder/'assembled.mkv'
    run_cmd(['ffmpeg','-v','error','-n','-f','concat','-safe','1','-i',str(listing),'-c','copy',str(video)],check=True)
    mapped.source_video = video
    transcript_path = folder/'assembled.transcript.json'
    write_json(transcript_path,mapped.to_dict())
    write_json(folder/'edit-decision-list.json',dict(source_video=str(Path(source).resolve()),
        source_clip=clip.to_dict(),duration=duration,timeline=timeline,
        note='Source-time map before optional silence shortening; retained original media.'))
    local_clip = replace(clip,start=0,end=duration,source_spans=[],contextual_overlays=[],b_roll_overlay=None)
    return video,mapped,local_clip,transcript_path
