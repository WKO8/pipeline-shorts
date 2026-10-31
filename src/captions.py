from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import pysubs2

from .config import AppConfig
from .models import Transcript, WordTimestamp
from .utils import ensure_parent_dir


@dataclass(slots=True)
class KineticCaptionStyle:
    font_name: str = "Montserrat"
    font_size: int = 120
    letter_spacing: float = 0.0
    primary_color: str = "#FFD700"
    keyword_color_a: str = "#FFD700"
    keyword_color_b: str = "#FFAA00"
    outline_color: str = "#DAA520"
    outline: float = 3.0
    shadow: float = 0.8
    popup_peak_frames: int = 3
    popup_settle_frames: int = 6
    fps: int = 30
    short_phrase_max_words: int = 2
    max_chars: int = 20
    keyword_glow_blur: float = 1.2
    pulse_color: str = "#FFAA00"
    hook_first_scale: int = 155
    hook_first_color: str = "#FFD700"
    force_uppercase: bool = True
    animated: bool = False
    position: str = "bottom"

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "KineticCaptionStyle":
        return cls(
            animated=cfg.captions_animated,
            position=cfg.captions_position,
            font_name=cfg.captions_font,
            font_size=cfg.captions_font_size,
            letter_spacing=0.0,
            primary_color=cfg.captions_color,
            keyword_color_a=cfg.captions_keyword_color_a,
            keyword_color_b=cfg.captions_keyword_color_b,
            outline_color=cfg.captions_stroke_color,
            outline=float(cfg.captions_stroke_width),
            shadow=cfg.captions_shadow if hasattr(cfg, "captions_shadow") else 0.8,
            popup_peak_frames=cfg.captions_popup_peak_frames,
            popup_settle_frames=cfg.captions_popup_settle_frames,
            fps=cfg.output_fps,
            short_phrase_max_words=cfg.captions_short_phrase_max_words,
            max_chars=cfg.captions_max_chars,
            keyword_glow_blur=cfg.captions_keyword_glow_blur,
            pulse_color=cfg.captions_active_word_color,
            hook_first_scale=cfg.captions_hook_first_scale,
            hook_first_color=cfg.captions_hook_first_color,
            force_uppercase=cfg.captions_force_uppercase,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KineticCaptionStyle":
        return cls(**{k: v for k, v in data.items() if k in cls.__annotations__})


@dataclass(slots=True)
class CaptionPlacement:
    """Optional placement for vertical remixes; legacy captions stay unchanged."""

    y_percent: float = 65.0
    safe_zones: bool = True

    def bounds(self) -> tuple[int, int, int, int]:
        return (54, 154, 918, 1536) if self.safe_zones else (0, 0, 1080, 1920)

    def tags(self, style: KineticCaptionStyle) -> str:
        if not 0 <= self.y_percent <= 100:
            raise ValueError("Caption Y must be between 0 and 100")
        left, top, right, bottom = self.bounds()
        # Reserve room for two lines and the largest existing popup animation.
        scale = max(120, style.hook_first_scale) / 100 if style.animated else 1
        padding = min((bottom-top)/2, style.font_size * scale + 12)
        y = round(max(top + padding, min(bottom - padding, 1920*self.y_percent/100)))
        return rf"{{\an5\pos({(left+right)//2},{y})\clip({left},{top},{right},{bottom})}}"


@dataclass(slots=True)
class CaptionBuildResult:
    ass_path: Path
    events_count: int
    srt_path: Optional[Path] = None


class BrainRotCaptionBuilder:
    """
    Word-by-word kinetic captions (ASS) using faster-whisper timestamps.
    """

    def __init__(self, style: KineticCaptionStyle | None = None) -> None:
        self.style = style or KineticCaptionStyle()

    def build(
        self,
        transcript: Transcript,
        output_path: str | Path,
        clip_start: float,
        clip_end: float,
        *,
        placement: CaptionPlacement | None = None,
    ) -> CaptionBuildResult:
        from .utils import preserve_existing
        preserve_existing(output_path)
        preserve_existing(Path(output_path).with_suffix(".srt"))
        segment_words = self._segments_for_clip(transcript, clip_start, clip_end)
        words = [word for group in segment_words for word in group]
        subtitle = pysubs2.SSAFile()
        subtitle.info["PlayResX"] = "1080"
        subtitle.info["PlayResY"] = "1920"
        subtitle.styles["Default"] = pysubs2.SSAStyle(
            fontname=self.style.font_name,
            fontsize=self.style.font_size,
            spacing=self.style.letter_spacing,
            primarycolor=pysubs2.Color(*self._hex_to_rgb(self.style.primary_color)),
            secondarycolor=pysubs2.Color(*self._hex_to_rgb(self.style.primary_color)),
            outlinecolor=pysubs2.Color(*self._hex_to_rgb(self.style.outline_color)),
            backcolor=pysubs2.Color(0, 0, 0, 120),
            bold=True,
            alignment=pysubs2.Alignment(2),
            borderstyle=1,
            outline=self.style.outline,
            shadow=self.style.shadow,
            marginv=300,
            marginl=40,
            marginr=40,
        )
        if placement is not None:
            left, _, right, _ = placement.bounds()
            subtitle.styles["Default"].marginl = left
            subtitle.styles["Default"].marginr = 1080 - right

        keyword_indexes = self._select_keyword_indexes(words)
        cursor = 0
        max_words = max(1, int(self.style.short_phrase_max_words))
        primary_ass = self._hex_to_ass(self.style.primary_color)
        active_ass = self._hex_to_ass(self.style.pulse_color)
        keyword_ass = self._hex_to_ass(self.style.keyword_color_a)

        for phrase in segment_words:
            if not phrase:
                continue
            for chunk in self._chunk_words(phrase, max_words, self.style.max_chars):
                phrase_start = cursor
                phrase_end = cursor + len(chunk)
                
                for i, active_word in enumerate(chunk):
                    # We want each word to stay until the next one starts within the chunk
                    start_ms, end_ms = self._word_window_ms(active_word, active_word)
                    if i < len(chunk) - 1:
                        next_word = chunk[i+1]
                        next_start_ms = int(round(next_word.start * 1000))
                        if next_start_ms > start_ms:
                            end_ms = min(next_start_ms, end_ms + 120)

                    text_parts = []
                    for j, word in enumerate(chunk):
                        token = (word.text or "").strip()
                        if not token:
                            continue
                        if self.style.force_uppercase:
                            token = token.upper()
                        token = self._ass_escape(token)

                        if i == j:
                            # Active word highlighting
                            is_keyword = (phrase_start + j) in keyword_indexes or self._is_highlight_word(word.text)
                            color_to_use = keyword_ass if is_keyword else active_ass
                            text_parts.append(rf"{{\1c{color_to_use}}}{token}{{\1c{primary_ass}}}")
                        else:
                            text_parts.append(token)
                    
                    text = " ".join(text_parts)
                    if not text.strip():
                        continue

                    active_is_keyword = (phrase_start + i) in keyword_indexes or self._is_highlight_word(active_word.text)
                    # The tag color should follow the active word highlight
                    line_color = self.style.keyword_color_a if active_is_keyword else self.style.pulse_color
                    
                    if self._is_background_sound_word(active_word.text):
                        line_color = self.style.keyword_color_b
                    
                    hormozi = self._is_hormozi_word(active_word.text)
                    tag = self._popup_tag(
                        line_color,
                        hormozi=hormozi,
                        first_hook=(len(subtitle.events) == 0),
                        stable=(i > 0),
                    )
                    
                    subtitle.events.append(
                        pysubs2.SSAEvent(
                            start=start_ms,
                            end=end_ms,
                            text=f"{tag}{placement.tags(self.style) if placement else ''}{text}",
                            style="Default",
                        )
                    )
                cursor = phrase_end

        output = ensure_parent_dir(output_path)
        subtitle.save(str(output), format_="ass")
        
        # Also generate SRT (clean version without ASS tags)
        srt_path = Path(output).with_suffix(".srt")
        srt_sub = copy.deepcopy(subtitle)
        for event in srt_sub.events:
            # Strip ASS tags for SRT: {\...}
            event.text = re.sub(r'\{[^}]+\}', '', event.text)
            # Remove line breaks that might have been added or literal \N
            event.text = event.text.replace(r'\N', ' ').replace('\n', ' ')
        
        srt_sub.save(str(srt_path), format_="srt")
        
        return CaptionBuildResult(
            ass_path=Path(output), 
            events_count=len(subtitle.events),
            srt_path=srt_path
        )

    @staticmethod
    def _segments_for_clip(
        transcript: Transcript,
        clip_start: float,
        clip_end: float,
    ) -> list[list[WordTimestamp]]:
        groups: list[list[WordTimestamp]] = []
        for segment in transcript.segments:
            if segment.end <= clip_start or segment.start >= clip_end:
                continue
            group = BrainRotCaptionBuilder._words_for_clip(
                segment.words, clip_start, clip_end
            )
            if group:
                groups.append(group)
        if groups:
            return groups
        fallback = BrainRotCaptionBuilder._words_for_clip(
            transcript.words, clip_start, clip_end
        )
        return [fallback] if fallback else []

    @staticmethod
    def _words_for_clip(
        words: Iterable[WordTimestamp], clip_start: float, clip_end: float
    ) -> list[WordTimestamp]:
        out: list[WordTimestamp] = []
        for w in words:
            if w.end <= clip_start or w.start >= clip_end:
                continue
            start = max(0.0, w.start - clip_start)
            end = max(start, min(clip_end, w.end) - clip_start)
            text = (w.text or "").strip()
            if not text:
                continue
            out.append(
                WordTimestamp(
                    text=text,
                    start=start,
                    end=end,
                    probability=w.probability,
                )
            )
        return out

    @staticmethod
    def _select_keyword_indexes(words: list[WordTimestamp]) -> set[int]:
        if not words:
            return set()
        durations = sorted(w.duration for w in words)
        p75 = durations[int(max(0, len(durations) * 0.75 - 1))]
        indexes: set[int] = set()
        for idx, w in enumerate(words):
            token = w.text.strip()
            if (
                len(token) >= 8
                or w.duration > p75
                or BrainRotCaptionBuilder._is_highlight_word(token)
            ):
                indexes.add(idx)
        return indexes

    @staticmethod
    def _chunk_words(
        words: list[WordTimestamp], max_words: int, max_chars: int = 0
    ) -> list[list[WordTimestamp]]:
        if not words:
            return []
        
        chunks = []
        current = []
        connectors = {"o", "a", "os", "as", "um", "uma", "de", "do", "da", "dos", "das",
                      "em", "no", "na", "nos", "nas", "por", "para", "com", "sem", "não"}
        for word in words:
            if current:
                pause = word.start - current[-1].end > 0.35 or current[-1].text.endswith((".", "!", "?"))
                size = sum(len(w.text.strip()) for w in current) + len(current) + len(word.text.strip())
                overflow = len(current) >= max_words or (max_chars > 0 and size > max_chars)
                if pause or overflow:
                    carry = []
                    if (not pause and len(current) > 1 and max_words > 1
                            and current[-1].text.casefold().strip() in connectors
                            and (not max_chars or len(current[-1].text) + 1 + len(word.text) <= max_chars)):
                        carry = [current[-1]]
                        current = current[:-1]
                    chunks.append(current)
                    current = carry
            current.append(word)
        if current:
            chunks.append(current)
        return chunks

    def _popup_tag(
        self,
        color: str,
        hormozi: bool = False,
        first_hook: bool = False,
        stable: bool = False,
    ) -> str:
        alignment = 5 if self.style.position == "center" else 2
        if not self.style.animated:
            return rf"{{\an{alignment}\fscx100\fscy100}}"
        peak_ms = 75
        settle_ms = 150
        color_ass = self._hex_to_ass(color)
        pulse_ass = self._hex_to_ass(self.style.pulse_color)
        outline_ass = self._hex_to_ass(self.style.outline_color)
        blur = 0.6
        shadow = self.style.shadow
        if color.strip().lower() == self.style.keyword_color_b.strip().lower():
            outline_ass = color_ass
            blur = self.style.keyword_glow_blur
            shadow = max(1.0, self.style.shadow)
        
        if stable:
            return (
                rf"{{\an{alignment}"
                rf"\1c{color_ass}"
                rf"\3c{outline_ass}"
                rf"\blur{blur:.1f}"
                rf"\shad{shadow:.1f}"
                r"\fscx100\fscy100}"
            )

        tag = (
            rf"{{\an{alignment}"
            rf"\1c{color_ass}"
            rf"\3c{outline_ass}"
            rf"\blur{blur:.1f}"
            rf"\shad{shadow:.1f}"
            r"\fscx80\fscy80"
            rf"\t(0,{peak_ms},\fscx120\fscy120)"
            rf"\t({peak_ms},{settle_ms},\fscx100\fscy100)"
        )
        if first_hook:
            hook_color = self._hex_to_ass(self.style.hook_first_color)
            hook_scale = max(110, self.style.hook_first_scale)
            tag += (
                rf"\1c{hook_color}\3c{hook_color}\blur{max(1.2, blur):.1f}\shad1.2"
                rf"\fscx{hook_scale}\fscy{hook_scale}"
                rf"\t(0,{settle_ms + 100},\fscx100\fscy100\1c{color_ass}\3c{outline_ass})"
            )
        if hormozi:
            shake_a = max(1, peak_ms // 2)
            shake_b = peak_ms + max(1, (settle_ms - peak_ms) // 2)
            tag += (
                rf"\t(0,{shake_a},\frz-2\1c{pulse_ass})"
                rf"\t({shake_a},{shake_b},\frz2\1c{color_ass})"
                rf"\t({shake_b},{settle_ms + 100},\frz0)"
            )
        return tag + r"}"

    def _phrase_color(
        self,
        chunk: list[WordTimestamp],
        start_idx: int,
        keyword_indexes: set[int],
    ) -> str:
        if any(self._is_background_sound_word(word.text) for word in chunk):
            return self.style.keyword_color_b
        for offset, word in enumerate(chunk):
            idx = start_idx + offset
            if self._is_highlight_word(word.text) or idx in keyword_indexes:
                return self.style.keyword_color_a
        return self.style.primary_color

    @staticmethod
    def _is_background_sound_word(text: str) -> bool:
        token = (text or "").strip().lower()
        if not token:
            return False
        if not (token.startswith("(") and token.endswith(")")):
            return False
        markers = ("risada", "risos", "aplausos", "murmur", "som")
        return any(marker in token for marker in markers)

    @staticmethod
    def _is_highlight_word(text: str) -> bool:
        token_raw = (text or "").strip()
        if not token_raw:
            return False
        if re.search(r"\d", token_raw):
            return True
        normalized = unicodedata.normalize("NFKD", token_raw)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^\w]+", "", normalized).lower()
        if not normalized:
            return False
        emphasis_words = {
            "negativo",
            "degenerado",
            "absurdo",
            "insano",
            "urgente",
            "chocante",
            "nunca",
            "jamais",
            "milhao",
            "milhoes",
            "jargao",
        }
        swear_words = {
            "porra",
            "caralho",
            "merda",
            "foda",
            "fdp",
            "cacete",
        }
        if normalized in emphasis_words or normalized in swear_words:
            return True
        return token_raw.isupper() and len(normalized) >= 3

    @staticmethod
    def _is_hormozi_word(text: str) -> bool:
        token = (text or "").strip()
        if not token:
            return False
        normalized = unicodedata.normalize("NFKD", token)
        normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
        normalized = re.sub(r"[^\w]+", "", normalized).upper()
        if not normalized:
            return False
        triggers = {
            "NUNCA",
            "ABSURDO",
            "MILHAO",
            "MILHOES",
            "CHOQUE",
            "SECRETO",
            "URGENTE",
            "INSANO",
        }
        return normalized in triggers

    @staticmethod
    def _word_window_ms(
        first_word: WordTimestamp, last_word: WordTimestamp
    ) -> tuple[int, int]:
        start_ms = int(round(first_word.start * 1000))
        end_ms = int(round(last_word.end * 1000))
        if end_ms <= start_ms:
            end_ms = start_ms + 80
        return start_ms, end_ms

    @staticmethod
    def _hex_to_rgb(color: str) -> tuple[int, int, int]:
        value = color.strip().lstrip("#")
        if len(value) != 6:
            value = "FFFFFF"
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))

    @staticmethod
    def _hex_to_ass(color: str) -> str:
        value = color.strip().lstrip("#")
        if len(value) != 6:
            value = "FFFFFF"
        rr = value[0:2]
        gg = value[2:4]
        bb = value[4:6]
        return f"&H00{bb}{gg}{rr}&"

    @staticmethod
    def _ass_escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").strip()
