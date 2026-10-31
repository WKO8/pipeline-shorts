"""Deterministic boundary checks and local, escaped review artifacts."""
from __future__ import annotations

from dataclasses import replace
import html
import math
import os
from pathlib import Path
from urllib.parse import quote

from .utils import write_json


def refine_render_clips(clips, transcript, config):
    """Move boundaries out of words, never trim a conclusion to meet duration."""
    refined = []
    for clip in clips:
        if clip.source_spans:
            from .models import SourceSpan
            # Each passage needs its own word-safe boundaries, not the envelope
            # spanning unrelated minutes of the episode.
            permissive = replace(config, clip_min_seconds=0, clip_max_seconds=float('inf'))
            parts = []
            for part in clip.source_spans:
                seed = replace(clip,start=part.start,end=part.end,source_spans=[])
                adjusted = refine_render_clips([seed],transcript,permissive)[0]
                parts.append(SourceSpan(adjusted.start,adjusted.end,part.role))
            body = [p for p in parts if p.role != 'hook']
            # Alignment may expand adjacent passages across a shared boundary.
            merged = []
            for part in body:
                if merged and part.start <= merged[-1].end:
                    merged[-1].end = max(merged[-1].end,part.end)
                else:
                    merged.append(part)
            parts = [p for p in parts if p.role=='hook'] + merged
            edited = replace(clip,start=min(p.start for p in parts),end=max(p.end for p in parts),source_spans=parts)
            if config.clip_min_seconds <= edited.duration <= config.clip_max_seconds:
                refined.append(edited)
            continue
        start, end = clip.start, clip.end
        for word in transcript.words:
            if word.start < start < word.end:
                start = word.start
            if word.start < end < word.end:
                end = word.end
        # Segment times may have expanded during alignment. Include the first/
        # last word if it is within the aligner's bounded recovery margin.
        padding = config.whisper_alignment_retry_padding
        for segment in transcript.segments:
            if segment.end <= clip.start or segment.start >= clip.end:
                continue
            if segment.words and abs(segment.start - clip.start) <= padding:
                start = min(start, segment.words[0].start)
            if segment.words and abs(segment.end - clip.end) <= padding:
                end = max(end, segment.words[-1].end)
        if not config.clip_min_seconds <= end - start <= config.clip_max_seconds:
            continue
        tags = list(clip.tags)
        if start != clip.start or end != clip.end:
            tags.append('word-boundary-adjusted')
        refined.append(replace(clip, start=max(0, start), end=end, tags=tags))
    return refined


def write_quality_review(directory, transcript, clips, rendered=(), source_video=None):
    """No services or uploads: JSON diagnostics plus playable local review page."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows, cards = [], []
    for index, clip in enumerate(clips):
        ranges = clip.source_ranges
        segments = [s for a,b in ranges for s in transcript.segments if s.end>a and s.start<b]
        words = [w for a,b in ranges for s in transcript.segments for w in s.words if w.end>a and w.start<b]
        warnings = []
        if any(s.text.strip() and not s.words for s in segments):
            warnings.append('Trecho sem tempos por palavra; conferir legenda.')
        invalid = sum(not math.isfinite(w.start) or not math.isfinite(w.end) or w.end <= w.start for w in words)
        if invalid:
            warnings.append(f'{invalid} palavra(s) com tempo inválido.')
        if not clip.source_spans and any(a.end > b.start + .04 for a, b in zip(words, words[1:])):
            warnings.append('Tempos de palavras sobrepostos; conferir sincronismo.')
        if any(w.start<a<w.end or w.start<b<w.end for a,b in ranges for w in words):
            warnings.append('Limite do corte atravessa uma palavra.')
        if 'review-incomplete' in clip.tags:
            warnings.append('Revisão editorial automática incompleta.')
        if not words:
            warnings.append('Sem palavras temporizadas neste corte.')
        if clip.source_spans:
            warnings.append('Montagem em ordem: '+ ' → '.join(f'{p.role}: {p.start:.2f}–{p.end:.2f}s' for p in clip.source_spans))
        output = Path(rendered[index].output_path) if index < len(rendered) else None
        rows.append(dict(index=index + 1, clip=clip.to_dict(), warnings=warnings,
                         word_count=len(words), output=str(output) if output else None))
        media = output or (Path(source_video) if source_video and not clip.source_spans else None)
        video = ''
        if output is None and source_video and clip.source_spans:
            base = quote(os.path.relpath(Path(source_video).resolve(),directory.resolve()),safe='/')
            video = ''.join(f'<p>Parte {i+1} ({html.escape(p.role)})</p><video controls preload="none" '
                f'src="{html.escape(base,quote=True)}#t={p.start:.3f},{p.end:.3f}"></video>'
                for i,p in enumerate(clip.source_spans))
        if media:
            url = quote(os.path.relpath(media.resolve(), directory.resolve()), safe='/')
            if output is None:
                url += f'#t={clip.start:.3f},{clip.end:.3f}'
            video = f'<video controls preload="none" src="{html.escape(url, quote=True)}"></video>'
        warning_html = ''.join(f'<li>{html.escape(w)}</li>' for w in warnings)
        cards.append(f'<article><h2>{index+1}. {html.escape(clip.title)}</h2>'
                     f'<p>{clip.start:.2f}–{clip.end:.2f}s · {clip.score:.1f}/10 (avaliação editorial)</p>'
                     f'{video}<p>{html.escape(clip.reason)}</p><ul>{warning_html}</ul>'
                     f'<details><summary>Transcrição</summary><p>{html.escape(" ".join(s.text for s in segments))}</p></details></article>')
    write_json(directory / 'quality-review.json', dict(timing_source=transcript.timing_source, clips=rows))
    document = ('<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width"><title>Revisão de cortes</title>'
                '<style>body{font:17px system-ui;max-width:900px;margin:32px auto;padding:0 20px;background:#14171c;color:#eef2f6}'
                'article{border:1px solid #46505b;border-radius:12px;padding:20px;margin:24px 0}'
                'video{width:100%;max-height:600px}li{color:#ffd886}p{line-height:1.6}</style>'
                '<h1>Revisão de cortes</h1><p>Arquivo local. Confira fala, contexto, enquadramento e legendas antes de publicar.</p>'
                + (''.join(cards) if cards else '<p>Nenhum candidato aprovado nesta execução. '
                   'Quando disponível, consulte <a href="diagnostic.json">o diagnóstico</a> para saber o motivo.</p>')
                + '</html>')
    (directory / 'review.html').write_text(document, encoding='utf-8')
    return directory / 'review.html'
