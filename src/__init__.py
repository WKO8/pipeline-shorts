"""
pipeline-shorts-build source package.

This package contains the full local pipeline for:
1) downloading a YouTube video/audio,
2) transcribing with word-level timestamps,
3) selecting viral moments with a local LLM or Jev (TypeSafe AI),
4) rendering vertical shorts with dynamic captions.
"""

from .youtube_discovery import YouTubeDiscoveryScraper
from .native_subtitles_extractor import extract_native_subtitles, native_segments_to_transcript
from .jev_curator import JevTwoLayerCurator
from .range_downloader import SelectiveRangeDownloader
from .console_ui import (
    console,
    print_header,
    print_stage,
    print_success,
    print_warning,
    print_error,
    print_thread,
    print_cuts_table,
    print_discovered_videos_table,
    print_timing_table,
)

__all__ = [
    "YouTubeDiscoveryScraper",
    "extract_native_subtitles",
    "native_segments_to_transcript",
    "JevTwoLayerCurator",
    "SelectiveRangeDownloader",
    "console",
    "print_header",
    "print_stage",
    "print_success",
    "print_warning",
    "print_error",
    "print_thread",
    "print_cuts_table",
    "print_discovered_videos_table",
    "print_timing_table",
]
