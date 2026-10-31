from __future__ import annotations

import logging
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import yt_dlp

from .models import Transcript, TranscriptSegment, WordTimestamp

LOGGER = logging.getLogger(__name__)


def extract_native_subtitles(
    url: str, preferred_langs: Optional[List[str]] = None
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Extracts native/auto YouTube subtitles in seconds without downloading media.
    Returns (segments, metadata). If no subtitles found, returns (None, metadata).
    Each segment is a dict with: {"start": float, "end": float, "text": str}.
    """
    preferred_langs = preferred_langs or ["pt-BR", "pt", "en", "en-US"]

    ydl_opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": preferred_langs,
        "quiet": True,
        "no_warnings": True,
        "js_runtimes": {"node": {}},
    }

    metadata: Dict[str, Any] = {
        "id": "",
        "title": "",
        "duration": 0,
        "uploader": "",
        "webpage_url": url,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None, metadata

            metadata = {
                "id": info.get("id") or "",
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "uploader": info.get("uploader", ""),
                "webpage_url": info.get("webpage_url", url),
            }

            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}

            # Prioritization of candidates:
            # 1. Manual subtitles matching preferred language
            # 2. Original automatic captions ending in '-orig' (e.g. pt-orig, en-orig)
            # 3. Standard automatic captions matching preferred language
            candidates_to_try = []
            for lang in preferred_langs:
                for k, v in subs.items():
                    if k.startswith(lang) and v:
                        candidates_to_try.append((f"{k} (manual)", v))
            for lang in preferred_langs:
                orig_key = f"{lang}-orig"
                if orig_key in auto_subs and auto_subs[orig_key]:
                    candidates_to_try.append((orig_key, auto_subs[orig_key]))
            for lang in preferred_langs:
                for k, v in auto_subs.items():
                    if k.startswith(lang) and v:
                        candidates_to_try.append((f"{k} (auto)", v))

            segments = None
            for c_name, formats in candidates_to_try:
                vtt_fmt = next((f for f in formats if f.get("ext") == "vtt"), None)
                if not vtt_fmt and formats:
                    vtt_fmt = formats[0]
                if not vtt_fmt or not vtt_fmt.get("url"):
                    continue

                try:
                    req = urllib.request.Request(
                        vtt_fmt["url"],
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status != 200:
                            continue
                        vtt_text = resp.read().decode("utf-8", errors="ignore")
                        if not vtt_text or len(vtt_text.strip()) < 50:
                            continue

                    parsed_segs = _parse_vtt_text(vtt_text)
                    if parsed_segs:
                        LOGGER.info(
                            "Extracted %d subtitle segments using '%s'",
                            len(parsed_segs),
                            c_name,
                        )
                        segments = parsed_segs
                        break
                except Exception as fetch_err:
                    LOGGER.debug("Failed fetching subtitle format %s: %s", c_name, fetch_err)
                    continue

            return segments, metadata

    except Exception as exc:
        LOGGER.warning("Error extracting subtitles from %s: %s", url, exc)
        return None, metadata


def _parse_vtt_text(vtt_text: str) -> List[Dict[str, Any]]:
    """
    Parses VTT string into a list of segments with start, end and clean text.
    """
    parsed_segs: List[Dict[str, Any]] = []
    time_pattern = re.compile(
        r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
    )

    def to_sec(h: str, m: str, s: str, ms: str) -> float:
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0

    lines = vtt_text.splitlines()
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None

    for line in lines:
        line_clean = line.strip()
        m = time_pattern.search(line_clean)
        if m:
            g = m.groups()
            cur_start = to_sec(g[0], g[1], g[2], g[3])
            cur_end = to_sec(g[4], g[5], g[6], g[7])
        elif cur_start is not None and line_clean and not line_clean.startswith("WEBVTT") and not line_clean.startswith("NOTE"):
            # Strip formatting/cue tags like <c>, <v Speaker>, etc.
            text = re.sub(r"<.*?>", "", line_clean).strip()
            text = re.sub(r"\[.*?\]", "", text).strip()
            if text:
                parsed_segs.append({
                    "start": round(cur_start, 2),
                    "end": round(cur_end, 2),
                    "text": text,
                })
            cur_start = None

    # Merge consecutive lines that have the exact same start and end, or identical texts
    deduped_segs: List[Dict[str, Any]] = []
    for s in parsed_segs:
        if deduped_segs and deduped_segs[-1]["text"] == s["text"]:
            deduped_segs[-1]["end"] = max(deduped_segs[-1]["end"], s["end"])
            continue
        deduped_segs.append(s)

    return deduped_segs


def native_segments_to_transcript(
    segments: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    language: str = "pt",
) -> Transcript:
    """
    Converts native subtitle segments to project Transcript model.
    """
    transcript_segments: List[TranscriptSegment] = []
    for idx, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        start = float(seg["start"])
        end = float(seg["end"])
        
        # Estimate word-level timestamps linearly across the segment duration
        words = text.split()
        word_objs: List[WordTimestamp] = []
        if words:
            dur_per_word = max(0.01, (end - start) / len(words))
            w_start = start
            for w in words:
                w_end = min(end, w_start + dur_per_word)
                word_objs.append(WordTimestamp(text=w, start=round(w_start, 2), end=round(w_end, 2)))
                w_start = w_end

        transcript_segments.append(
            TranscriptSegment(
                id=idx,
                start=start,
                end=end,
                text=text,
                words=word_objs,
            )
        )

    return Transcript(
        language=language,
        timing_source="native_vtt",
        segments=transcript_segments,
    )


def slice_segments_for_cut(
    segments: List[Dict[str, Any]],
    cut_start: float,
    cut_end: float,
    language: str = "pt",
) -> Transcript:
    """
    Slices a subset of segments matching [cut_start, cut_end] and shifts
    timestamps relative to cut_start (so cut begins at 0.0s).
    """
    sliced_segments: List[TranscriptSegment] = []
    seg_idx = 1
    duration = max(0.1, cut_end - cut_start)

    for seg in segments:
        s_start = float(seg["start"])
        s_end = float(seg["end"])
        if s_end <= cut_start or s_start >= cut_end:
            continue

        rel_start = max(0.0, s_start - cut_start)
        rel_end = min(duration, s_end - cut_start)
        if rel_end <= rel_start:
            continue

        text = seg["text"].strip()
        words = text.split()
        word_objs: List[WordTimestamp] = []
        if words:
            dur_per_word = max(0.01, (rel_end - rel_start) / len(words))
            w_start = rel_start
            for w in words:
                w_end = min(rel_end, w_start + dur_per_word)
                word_objs.append(WordTimestamp(text=w, start=round(w_start, 2), end=round(w_end, 2)))
                w_start = w_end

        sliced_segments.append(
            TranscriptSegment(
                id=seg_idx,
                start=round(rel_start, 2),
                end=round(rel_end, 2),
                text=text,
                words=word_objs,
            )
        )
        seg_idx += 1

    return Transcript(
        language=language,
        timing_source="native_vtt_sliced",
        segments=sliced_segments,
    )
