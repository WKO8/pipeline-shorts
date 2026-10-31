from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from typesafe_sdk import AsyncTypeSafeClient, Noul

from .models import ClipCandidate

LOGGER = logging.getLogger(__name__)


class JevTwoLayerCurator:
    """
    Two-layer cognitive curation powered by Jev (TypeSafe AI):
    - Layer 1: 60-second hook evaluation via System One (discards ~80% warm content).
    - Layer 2: 3-5 minute narrative expansion, sentence-end boundary alignment,
               >50% overlap deduplication, and narrative retention scoring.
    - Final ranking: combined_score = 0.4 * hook + 0.6 * narrative.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "jev-latest",
        max_concurrent: int = 20,
        client: Optional[AsyncTypeSafeClient] = None,
    ) -> None:
        self.api_key = api_key or ""
        self.model = model
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.client = client or (AsyncTypeSafeClient(api_key=self.api_key) if self.api_key else None)

    # -------------------------------------------------------------------------
    # Layer 1: Hooks Curation (60s chunks, 80% reduction)
    # -------------------------------------------------------------------------

    async def _score_hook(self, hook: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluates a hook window using Jev System One with viral curiosity criteria."""
        if not self.client:
            LOGGER.warning("No TypeSafe client/API key available. Hook score set to 0.0.")
            hook["hook_score"] = 0.0
            return hook

        async with self.semaphore:
            state_text = (
                f"Trecho inicial (tempo: {hook['start_second']:.1f}s a {hook['end_second']:.1f}s):\n"
                f"{hook['text']}\n"
            )
            questions = {
                "hook_interest": Noul(
                    instructions=(
                        "Avalie o potencial de gancho (hook) para redes sociais (Shorts/Reels/TikTok) deste início de vídeo. "
                        "Ele desperta curiosidade imediata, traz uma fala marcante, número surpreendente, dilema intrigante "
                        "ou quebra de padrão capaz de fazer o espectador parar de rolar o feed e querer ver a continuação?"
                    ),
                    criteria={
                        "true": "Início atraente com forte curiosidade, provocação ou gancho magnético nos primeiros segundos.",
                        "false": "Início morno, comum, bate-papo cotidiano ou sem atrativo inicial.",
                    },
                )
            }
            try:
                res = await self.client.system_one(
                    state=state_text, model=self.model, questions=questions
                )
                score = float(res.answers["hook_interest"].noul)
                hook["hook_score"] = round(score, 3)
            except Exception as err:
                LOGGER.warning("Error evaluating hook [%.1f-%.1f]: %s", hook["start_second"], hook["end_second"], err)
                hook["hook_score"] = 0.0

            return hook

    async def curate_hooks(
        self,
        segments: List[Dict[str, Any]],
        window_size: float = 45.0,
        stride: Optional[float] = None,
        keep_ratio: float = 0.20,
        min_score: float = 0.35,
        min_keep: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Splits segments into hook windows (with sliding window stride),
        concurrently scores them with Jev, and keeps only the top performers.
        """
        if not segments:
            return []

        max_time = max(float(s["end"]) for s in segments)
        if max_time <= 0:
            return []

        # Use 20s sliding window stride by default for window_size <= 45s, or window_size if 60s+
        step = stride if stride is not None else (20.0 if window_size <= 45.0 else window_size)

        # 1. Build sliding hook windows
        raw_hooks: List[Dict[str, Any]] = []
        cur_start = 0.0
        hook_idx = 1
        while cur_start < max_time:
            cur_end = min(max_time, cur_start + window_size)
            w_segs = [
                s for s in segments
                if float(s["end"]) > cur_start and float(s["start"]) < cur_end
            ]
            w_text = " ".join(s["text"].strip() for s in w_segs if s.get("text", "").strip())
            if w_text and len(w_text.strip()) > 0:
                raw_hooks.append({
                    "hook_id": hook_idx,
                    "start_second": round(cur_start, 2),
                    "end_second": round(cur_end, 2),
                    "duration": round(cur_end - cur_start, 2),
                    "text": w_text,
                })
                hook_idx += 1
            cur_start += step

        if not raw_hooks:
            return []

        LOGGER.info("Layer 1: Scoring %d hook window(s) (window=%.1fs, step=%.1fs) with Jev...", len(raw_hooks), window_size, step)
        tasks = [self._score_hook(h) for h in raw_hooks]
        evaluated_hooks = await asyncio.gather(*tasks)

        # If sliding window was used with step < window_size, deduplicate contiguous overlapping hooks
        if step < window_size:
            evaluated_hooks_sorted = sorted(evaluated_hooks, key=lambda h: h["hook_score"], reverse=True)
            deduped_hooks: List[Dict[str, Any]] = []
            for h in evaluated_hooks_sorted:
                # Discard if too close to an already accepted higher-scoring hook start
                if any(abs(h["start_second"] - prev["start_second"]) < step for prev in deduped_hooks):
                    continue
                deduped_hooks.append(h)
            candidates_pool = deduped_hooks
        else:
            candidates_pool = evaluated_hooks

        # 2. Filter top hooks
        sorted_hooks = sorted(candidates_pool, key=lambda h: h["hook_score"], reverse=True)
        target_count = max(1, math.ceil(len(candidates_pool) * keep_ratio))
        top_slice = sorted_hooks[:target_count]
        kept = [h for h in top_slice if h["hook_score"] >= min_score]
        effective_min_keep = max(1, min(len(top_slice), min_keep))
        if len(kept) < effective_min_keep:
            kept = top_slice[:effective_min_keep]

        # 3. Reorder chronologically
        kept.sort(key=lambda h: h["start_second"])
        LOGGER.info(
            "Layer 1: Retained %d top hook(s) out of %d (%.0f%% reduction)",
            len(kept),
            len(evaluated_hooks),
            (1.0 - len(kept) / len(evaluated_hooks)) * 100,
        )
        return kept

    # -------------------------------------------------------------------------
    # Layer 2: Narrative Expansion and Evaluation (20s to 180s windows)
    # -------------------------------------------------------------------------

    @staticmethod
    def _find_natural_sentence_end(
        start_sec: float,
        segments: List[Dict[str, Any]],
        min_duration: float = 20.0,
        max_duration: float = 180.0,
    ) -> Tuple[float, str]:
        """
        Finds the ideal end timestamp and text for a window starting at start_sec,
        identifying a natural narrative completion (Gancho -> Contexto -> Desfecho)
        between min_duration (default 20s) and max_duration (default 180s).
        """
        target_min_end = start_sec + min_duration
        target_max_end = start_sec + max_duration

        CONTINUATION_REGEX = re.compile(
            r"^(>>\s*|&gt;&gt;\s*)?(aí|e aí|depois|mas|porém|porque|por que|então|quando|só que|até que|daí|e também|além disso)\b",
            re.IGNORECASE,
        )
        TOPIC_SHIFT_REGEX = re.compile(
            r"^(>>\s*|&gt;&gt;\s*)?([A-ZÀ-Ú].*\?|como |onde |qual |quem |quantos |quantas |por que |por quê |vamos falar|mudando de assunto|entendi, mas)",
            re.IGNORECASE,
        )
        CONCLUSION_REGEX = re.compile(
            r"\b(é isso|foi isso|então é isso|no final do dia|no final das contas|resultado|"
            r"moral da história|por isso|com certeza|exatamente|foi assim que|essa foi a virada|"
            r"mudou tudo|esse é o segredo|não tem segredo|hoje eu|hoje a gente|virou o jogo|"
            r"deu certo|virou a chave|pro meu negócio|pro seu negócio|faturamos|faturamento|"
            r"acabou|conseguimos|desbloquear o mundo)\b",
            re.IGNORECASE,
        )

        candidate_ends: List[Tuple[float, float]] = []

        for i, s in enumerate(segments):
            s_end = float(s["end"])
            if s_end < target_min_end:
                continue
            if s_end > target_max_end:
                break

            text = s.get("text", "").strip()
            # Videos should not end right after a question (inquiries demand answers)
            if text.endswith("?"):
                continue
            # Check if segment ends with sentence-ending punctuation (. or !)
            if not re.search(r"[.!]+[\"'\u201d\u2019]?$", text):
                continue

            dur = s_end - start_sec
            closure_score = 2.0

            if i + 1 < len(segments):
                next_s = segments[i + 1]
                next_text = next_s.get("text", "").strip()
                # 1. Heavily penalize if next segment is an obvious continuation of the same thought
                if CONTINUATION_REGEX.search(next_text):
                    closure_score -= 8.0

                # 2. Reward if next segment represents a topic shift, host question, or new theme
                if TOPIC_SHIFT_REGEX.search(next_text) or next_text.endswith("?"):
                    closure_score += 8.0

                # 3. Reward conversational pauses / silence gap
                gap = float(next_s["start"]) - s_end
                if gap >= 0.4:
                    closure_score += 3.0

            # 4. Conclusion / punchline keyword bonus
            if CONCLUSION_REGEX.search(text):
                closure_score += 6.0

            # 5. Ideal duration window for short-form narrative (35s to 100s)
            if 35.0 <= dur <= 100.0:
                closure_score += 4.0
            elif 25.0 <= dur < 35.0 or 100.0 < dur <= 140.0:
                closure_score += 2.0

            candidate_ends.append((s_end, closure_score))

        best_end = None
        if candidate_ends:
            # Sort by closure score descending; prefer candidates with positive closure score
            candidate_ends.sort(key=lambda x: x[1], reverse=True)
            best_end = candidate_ends[0][0]

        # If no sentence end found in zone, pick the last segment ending before target_max_end
        if best_end is None:
            before_max = [s for s in segments if float(s["end"]) <= target_max_end and float(s["end"]) >= target_min_end]
            if before_max:
                best_end = float(before_max[-1]["end"])
            else:
                best_end = min(max_duration + start_sec, float(segments[-1]["end"]))

        # Gather all text between start_sec and best_end
        full_segs = [
            s for s in segments
            if float(s["end"]) > start_sec and float(s["start"]) < best_end
        ]
        full_text = " ".join(s["text"].strip() for s in full_segs if s.get("text", "").strip())
        return round(best_end, 2), full_text

    @staticmethod
    def _calculate_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
        """Calculates overlap ratio relative to the shorter duration."""
        intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
        shorter = min(a_end - a_start, b_end - b_start)
        if shorter <= 0:
            return 0.0
        return intersection / shorter

    def _deduplicate_narrative_windows(
        self, candidates: List[Dict[str, Any]], max_overlap: float = 0.50
    ) -> List[Dict[str, Any]]:
        """
        Deduplicates candidate windows with overlap > 50%, keeping the one
        with the higher hook_score.
        """
        # Sort candidates by hook_score descending
        sorted_cands = sorted(candidates, key=lambda c: c["hook_score"], reverse=True)
        accepted: List[Dict[str, Any]] = []

        for cand in sorted_cands:
            overlap = False
            for prev in accepted:
                ratio = self._calculate_overlap(
                    cand["start_second"], cand["end_second"],
                    prev["start_second"], prev["end_second"],
                )
                if ratio > max_overlap:
                    overlap = True
                    break
            if not overlap:
                accepted.append(cand)

        # Restore chronological order for further processing
        accepted.sort(key=lambda c: c["start_second"])
        return accepted

    async def _score_narrative(self, narrative: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluates narrative structure, complete arc, and retention on Jev."""
        if not self.client:
            LOGGER.warning("No TypeSafe client/API key available. Narrative score set to 0.0.")
            narrative["narrative_score"] = 0.0
            narrative["combined_score"] = round(0.40 * narrative["hook_score"], 3)
            return narrative

        async with self.semaphore:
            state_text = (
                f"Trecho de {narrative['duration_seconds']:.1f}s "
                f"(de {narrative['start_second']:.1f}s a {narrative['end_second']:.1f}s):\n"
                f"{narrative['full_transcript']}\n"
            )
            questions = {
                "narrative_quality": Noul(
                    instructions=(
                        f"Avalie a qualidade narrativa deste clipe de vídeo para redes sociais ({narrative['duration_seconds']:.0f}s). "
                        "O trecho apresenta uma história, reflexão ou explicação interessante e envolvente, com início claro, "
                        "desenvolvimento que prende a atenção e conclusão/punchline satisfatória sem corte abrupto no meio?"
                    ),
                    criteria={
                        "true": "História ou explicação envolvente, com bom ritmo e conclusão satisfatória para vídeo curto.",
                        "false": "Trecho confuso, arrastado, desinteressante, cortado no meio ou sem conclusão.",
                    },
                )
            }
            try:
                res = await self.client.system_one(
                    state=state_text, model=self.model, questions=questions
                )
                score = float(res.answers["narrative_quality"].noul)
                narrative["narrative_score"] = round(score, 3)
            except Exception as err:
                LOGGER.warning("Error evaluating narrative [%.1f-%.1f]: %s", narrative["start_second"], narrative["end_second"], err)
                narrative["narrative_score"] = 0.0

            # Combined score: 40% hook + 60% narrative
            hook_s = narrative["hook_score"]
            narr_s = narrative["narrative_score"]
            narrative["combined_score"] = round((0.40 * hook_s) + (0.60 * narr_s), 3)

            return narrative

    async def expand_and_evaluate_narratives(
        self,
        top_hooks: List[Dict[str, Any]],
        segments: List[Dict[str, Any]],
        min_duration: float = 20.0,
        max_duration: float = 180.0,
    ) -> List[Dict[str, Any]]:
        """
        Expands top hooks into narrative windows (20s to 180s), aligns sentence end,
        deduplicates overlaps > 50%, and scores narrative quality on Jev.
        """
        if not top_hooks or not segments:
            return []

        # 1. Expand hooks into narrative windows with natural sentence ends
        max_time = max((float(s["end"]) for s in segments), default=0.0)
        candidates: List[Dict[str, Any]] = []
        for h in top_hooks:
            start_s = float(h["start_second"])
            end_s, text = self._find_natural_sentence_end(
                start_sec=start_s,
                segments=segments,
                min_duration=min_duration,
                max_duration=max_duration,
            )
            duration_s = round(end_s - start_s, 2)
            if max_time >= min_duration and duration_s < (min_duration * 0.5):
                continue
            candidates.append({
                "hook_id": h.get("hook_id", 0),
                "start_second": start_s,
                "end_second": end_s,
                "duration_seconds": duration_s,
                "hook_score": h["hook_score"],
                "full_transcript": text,
            })

        if not candidates and top_hooks:
            for h in top_hooks:
                start_s = float(h["start_second"])
                end_s, text = self._find_natural_sentence_end(
                    start_sec=start_s,
                    segments=segments,
                    min_duration=min_duration,
                    max_duration=max_duration,
                )
                candidates.append({
                    "hook_id": h.get("hook_id", 0),
                    "start_second": start_s,
                    "end_second": end_s,
                    "duration_seconds": round(end_s - start_s, 2),
                    "hook_score": h["hook_score"],
                    "full_transcript": text,
                })

        # 2. Deduplicate overlaps > 50%
        deduped = self._deduplicate_narrative_windows(candidates, max_overlap=0.50)
        LOGGER.info(
            "Layer 2: Scoring %d narrative window(s) after deduplication...",
            len(deduped),
        )

        # 3. Concurrently score narratives on Jev
        tasks = [self._score_narrative(c) for c in deduped]
        scored_narratives = await asyncio.gather(*tasks)

        # 4. Sort by combined_score descending
        scored_narratives.sort(key=lambda x: x["combined_score"], reverse=True)
        for idx, cut in enumerate(scored_narratives, start=1):
            cut["cut_id"] = idx

        return scored_narratives

    # -------------------------------------------------------------------------
    # Full Two-Layer Orchestration
    # -------------------------------------------------------------------------

    async def curate(
        self,
        segments: List[Dict[str, Any]],
        window_size: float = 45.0,
        keep_ratio: float = 0.20,
        min_hook_score: float = 0.35,
        min_duration: float = 20.0,
        max_duration: float = 180.0,
        max_clips: Optional[int] = None,
        stride: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Runs both layers end-to-end and returns the final curated viral cuts.
        """
        min_keep = max_clips if (max_clips and max_clips > 0) else 5
        layer1_min_keep = max(15, min_keep * 3)
        top_hooks = await self.curate_hooks(
            segments=segments,
            window_size=window_size,
            stride=stride,
            keep_ratio=keep_ratio,
            min_score=min_hook_score,
            min_keep=layer1_min_keep,
        )
        if not top_hooks:
            LOGGER.warning("No hooks met the criteria in Layer 1.")
            return []

        final_cuts = await self.expand_and_evaluate_narratives(
            top_hooks=top_hooks,
            segments=segments,
            min_duration=min_duration,
            max_duration=max_duration,
        )

        if max_clips and max_clips > 0:
            final_cuts = final_cuts[:max_clips]
            for idx, cut in enumerate(final_cuts, start=1):
                cut["cut_id"] = idx

        return final_cuts

    # -------------------------------------------------------------------------
    # Output formatting and export
    # -------------------------------------------------------------------------

    def save_viral_cuts_json(
        self,
        cuts: List[Dict[str, Any]],
        output_path: Path,
        video_id: str,
        title: str,
    ) -> Path:
        """
        Saves curated cuts adhering to Schema 9.2.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": {
                "video_id": video_id,
                "title": title,
                "total_cuts_found": len(cuts),
            },
            "viral_cuts": cuts,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Saved viral cuts JSON to %s", output_path)
        return output_path

    @staticmethod
    def to_clip_candidates(
        cuts: List[Dict[str, Any]],
        title_prefix: str = "Corte Viral",
    ) -> List[ClipCandidate]:
        """
        Converts curated viral cuts to project ClipCandidate objects
        for native integration into the rendering pipeline.
        """
        candidates: List[ClipCandidate] = []
        for c in cuts:
            title = f"{title_prefix} {c['cut_id']} ({c['start_second']:.0f}s-{c['end_second']:.0f}s)"
            candidates.append(
                ClipCandidate(
                    title=title,
                    start=float(c["start_second"]),
                    end=float(c["end_second"]),
                    score=float(c["combined_score"]),
                    reason=(
                        f"Gancho Jev: {c['hook_score']:.2f} | "
                        f"Narrativa Jev: {c['narrative_score']:.2f} | "
                        f"Score Combinado: {c['combined_score']:.2f}"
                    ),
                    tags=["jev", "viral-cut"],
                )
            )
        return candidates
