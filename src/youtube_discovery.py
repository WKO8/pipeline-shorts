from __future__ import annotations

import asyncio
import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import yt_dlp
from typesafe_sdk import AsyncTypeSafeClient, Noul

LOGGER = logging.getLogger(__name__)


class YouTubeDiscoveryScraper:
    """
    Scrapes long-form YouTube videos (10m to 4h) by query or trending seeds,
    and concurrently evaluates their viral potential and topic relevance with Jev.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "jev-latest",
        max_concurrent: int = 15,
        client: Optional[AsyncTypeSafeClient] = None,
    ) -> None:
        self.api_key = api_key or ""
        self.model = model
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.client = client or (AsyncTypeSafeClient(api_key=self.api_key) if self.api_key else None)
        self.ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "skip_download": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }

    def search_candidates(
        self,
        query: Optional[str] = None,
        limit: int = 15,
        min_duration_sec: int = 600,  # Minimum 10 min
        max_duration_sec: int = 14400,  # Maximum 4 hours
    ) -> List[Dict[str, Any]]:
        """
        Extracts candidate metadata without downloading media.
        """
        raw_entries: List[Dict[str, Any]] = []
        with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
            if query and query.strip():
                target = f"ytsearch{limit * 2}:{query.strip()}"
                LOGGER.info("Searching YouTube for query: %s", query.strip())
                try:
                    info = ydl.extract_info(target, download=False)
                    if info and "entries" in info:
                        raw_entries.extend(info["entries"])
                except Exception as exc:
                    LOGGER.warning("Error searching YouTube query '%s': %s", query, exc)
            else:
                seeds = [
                    "ytsearch15:podcast em alta brasil",
                    "ytsearch15:entrevista reveladora podcast",
                ]
                LOGGER.info("Searching YouTube trending podcast seeds...")
                for seed in seeds:
                    try:
                        info = ydl.extract_info(seed, download=False)
                        if info and "entries" in info:
                            raw_entries.extend(info["entries"])
                    except Exception as exc:
                        LOGGER.warning("Error searching seed '%s': %s", seed, exc)

        candidates: List[Dict[str, Any]] = []
        seen = set()
        for e in raw_entries:
            if not e:
                continue
            v_id = e.get("id")
            duration = e.get("duration")
            if not v_id or v_id in seen or duration is None:
                continue
            try:
                duration_int = int(duration)
            except (TypeError, ValueError):
                continue

            if duration_int < min_duration_sec or duration_int > max_duration_sec:
                continue

            seen.add(v_id)
            url = e.get("url") or f"https://www.youtube.com/watch?v={v_id}"
            if not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={v_id}"

            hrs = duration_int // 3600
            mins = (duration_int % 3600) // 60
            formatted_duration = f"{hrs}h {mins}m" if hrs > 0 else f"{mins}m"

            candidates.append(
                {
                    "video_id": v_id,
                    "url": url,
                    "title": (e.get("title") or "").strip(),
                    "channel": e.get("uploader") or e.get("channel") or "Desconhecido",
                    "duration_seconds": duration_int,
                    "duration_formatted": formatted_duration,
                    "view_count": e.get("view_count") or 0,
                    "description_snippet": ((e.get("description") or "")[:400]).strip(),
                }
            )
            if len(candidates) >= limit:
                break

        LOGGER.info("Found %d candidate video(s) within duration limits", len(candidates))
        return candidates

    async def _score_video_candidate(
        self, video: Dict[str, Any], topic: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Queries Jev for viral potential and topic relevance scores.
        """
        if not self.client:
            LOGGER.warning("No TypeSafe client/API key available. Setting score to 0.0.")
            video["jev_viral_score"] = 0.0
            video["jev_topic_relevance"] = 0.0 if topic else None
            video["jev_score"] = 0.0
            return video

        async with self.semaphore:
            state_text = (
                f"Título: {video['title']}\n"
                f"Canal: {video['channel']}\n"
                f"Visualizações: {video['view_count']:,}\n"
                f"Duração: {video['duration_seconds']}s\n"
                f"Descrição: {video['description_snippet']}\n"
            )
            if topic:
                state_text += f"Tema Solicitado: {topic}\n"

            questions = {
                "viral_potential": Noul(
                    instructions=(
                        "Avalie em uma escala de 0 a 1 o potencial deste vídeo longo gerar cortes curtos "
                        "virais de alto impacto para TikTok, Reels e Shorts. Ele trata de temas polêmicos, "
                        "curiosidades magnéticas, histórias envolventes, lições de vida, revelações ou "
                        "debates acalorados que prendem imediatamente o público (1.0), ou é um vídeo "
                        "monótono, técnico demais, institucional ou corporativo sem apelo viral (0.0)?"
                    ),
                    criteria={
                        "true": "Vídeo excelente para cortes virais: convidados fortes, assunto magnético, revelações.",
                        "false": "Vídeo corporativo, monótono, didático/técnico demais ou com pouca retenção.",
                    },
                )
            }

            if topic:
                questions["topic_relevance"] = Noul(
                    instructions=(
                        f"Avalie em uma escala de 0 a 1 o quanto este vídeo trata diretamente, com foco e "
                        f"relevância, do tema: '{topic}'."
                    ),
                    criteria={
                        "true": f"O vídeo aborda centralmente o tema '{topic}'.",
                        "false": f"O vídeo é sobre outro assunto ou apenas cita '{topic}' de passagem.",
                    },
                )

            try:
                res = await self.client.system_one(
                    state=state_text, model=self.model, questions=questions
                )
                viral_score = float(res.answers["viral_potential"].noul)
                if topic and "topic_relevance" in res.answers:
                    rel_score = float(res.answers["topic_relevance"].noul)
                    combined = round((0.55 * viral_score) + (0.45 * rel_score), 3)
                else:
                    rel_score = 1.0
                    combined = round(viral_score, 3)

                video["jev_viral_score"] = round(viral_score, 3)
                video["jev_topic_relevance"] = round(rel_score, 3) if topic else None
                video["jev_score"] = combined
                if topic:
                    video["theme"] = topic
            except Exception as err:
                LOGGER.warning("Error scoring video %s with Jev: %s", video.get("video_id"), err)
                video["jev_viral_score"] = 0.0
                video["jev_topic_relevance"] = 0.0 if topic else None
                video["jev_score"] = 0.0

            return video

    async def discover_and_rank(
        self,
        query: Optional[str] = None,
        limit: int = 15,
        min_score: float = 0.60,
    ) -> List[Dict[str, Any]]:
        """
        Searches candidates, evaluates them concurrently with Jev,
        and returns them ranked by descending score.
        """
        candidates = self.search_candidates(query=query, limit=limit)
        if not candidates:
            return []

        tasks = [self._score_video_candidate(c, topic=query) for c in candidates]
        evaluated = await asyncio.gather(*tasks)

        kept = [v for v in evaluated if v.get("jev_score", 0.0) >= min_score]
        if not kept and evaluated:
            kept = [max(evaluated, key=lambda x: x.get("jev_score", 0.0))]

        kept.sort(key=lambda x: x.get("jev_score", 0.0), reverse=True)
        for rank, v in enumerate(kept, start=1):
            v["rank"] = rank

        return kept

    def save_curated_videos_json(
        self,
        videos: List[Dict[str, Any]],
        output_path: Path,
        search_query: Optional[str] = None,
        total_analyzed: Optional[int] = None,
        min_score: float = 0.60,
    ) -> Path:
        """
        Saves curated video candidates matching Schema 9.1.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "metadata": {
                "search_query": search_query or "trending",
                "total_candidates_analyzed": total_analyzed if total_analyzed is not None else len(videos),
                "total_curated_kept": len(videos),
                "min_score_threshold": min_score,
            },
            "curated_videos": videos,
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Saved curated videos JSON to %s", output_path)
        return output_path
