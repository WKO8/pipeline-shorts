from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.youtube_discovery import YouTubeDiscoveryScraper


class FakeNoulAnswer:
    def __init__(self, val: float):
        self.noul = val


class FakeSystemOneResponse:
    def __init__(self, answers: dict):
        self.answers = answers


class FakeTypeSafeClient:
    async def system_one(self, state: str, model: str, questions: dict):
        answers = {}
        is_low = "Low Score" in state
        viral = 0.60 if is_low else 0.85
        relevance = 0.50 if is_low else 0.90
        if "viral_potential" in questions:
            answers["viral_potential"] = FakeNoulAnswer(viral)
        if "topic_relevance" in questions:
            answers["topic_relevance"] = FakeNoulAnswer(relevance)
        return FakeSystemOneResponse(answers)


def test_search_candidates_filters_duration_and_deduplicates():
    scraper = YouTubeDiscoveryScraper(api_key="test_key")

    mock_entries = [
        {"id": "short_vid", "duration": 300, "title": "Too Short"},  # 5 min -> reject
        {"id": "long_vid", "duration": 20000, "title": "Too Long"},  # > 4h -> reject
        {
            "id": "valid_1",
            "duration": 3600,
            "title": "Valid Podcast 1",
            "uploader": "Host A",
            "view_count": 50000,
            "description": "Awesome interview",
        },
        {
            "id": "valid_1",  # duplicate id -> reject
            "duration": 3600,
            "title": "Valid Podcast 1 Duplicate",
        },
        {
            "id": "valid_2",
            "duration": 1800,
            "title": "Valid Podcast 2",
            "uploader": "Host B",
            "view_count": 120000,
            "description": "Tech news",
        },
    ]

    with patch("yt_dlp.YoutubeDL") as mock_ydl_cls:
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"entries": mock_entries}
        mock_ydl_cls.return_value.__enter__.return_value = mock_instance

        candidates = scraper.search_candidates(query="ai podcast", limit=10)

        assert len(candidates) == 2
        assert candidates[0]["video_id"] == "valid_1"
        assert candidates[0]["duration_formatted"] == "1h 0m"
        assert candidates[1]["video_id"] == "valid_2"
        assert candidates[1]["duration_formatted"] == "30m"


@pytest.mark.anyio
async def test_score_video_candidate_with_topic():
    fake_client = FakeTypeSafeClient()
    scraper = YouTubeDiscoveryScraper(api_key="test_key", client=fake_client)

    video = {
        "video_id": "v123",
        "title": "Debate IA",
        "channel": "Canal Tech",
        "view_count": 10000,
        "duration_seconds": 1800,
        "description_snippet": "Discussão sobre IA",
    }

    scored = await scraper._score_video_candidate(video, topic="inteligencia artificial")
    assert scored["jev_viral_score"] == 0.85
    assert scored["jev_topic_relevance"] == 0.90
    # Combined = 0.55 * 0.85 + 0.45 * 0.90 = 0.4675 + 0.405 = 0.8725 -> 0.873
    assert scored["jev_score"] == 0.873
    assert scored["theme"] == "inteligencia artificial"


@pytest.mark.anyio
async def test_discover_and_rank_sorts_and_filters():
    fake_client = FakeTypeSafeClient()
    scraper = YouTubeDiscoveryScraper(api_key="test_key", client=fake_client)

    dummy_candidates = [
        {
            "video_id": "v1",
            "url": "https://youtube.com/watch?v=v1",
            "title": "Low Score Video",
            "channel": "Channel 1",
            "duration_seconds": 1200,
            "duration_formatted": "20m",
            "view_count": 500,
            "description_snippet": "Boring",
        },
        {
            "video_id": "v2",
            "url": "https://youtube.com/watch?v=v2",
            "title": "High Score Video",
            "channel": "Channel 2",
            "duration_seconds": 3600,
            "duration_formatted": "1h 0m",
            "view_count": 50000,
            "description_snippet": "Exciting",
        },
    ]

    scraper.search_candidates = MagicMock(return_value=dummy_candidates)

    ranked = await scraper.discover_and_rank(query="test", limit=5, min_score=0.50)
    assert len(ranked) == 2
    assert ranked[0]["rank"] == 1
    assert ranked[0]["video_id"] == "v2"
    assert ranked[0]["jev_score"] > 0


def test_save_curated_videos_json(tmp_path: Path):
    scraper = YouTubeDiscoveryScraper(api_key="test_key")
    out_file = tmp_path / "curated_videos_test.json"

    sample_videos = [
        {
            "video_id": "ex123",
            "url": "https://www.youtube.com/watch?v=ex123",
            "title": "Sample Title",
            "channel": "Channel",
            "duration_seconds": 1800,
            "duration_formatted": "30m",
            "view_count": 10000,
            "jev_viral_score": 0.85,
            "jev_topic_relevance": 0.90,
            "jev_score": 0.873,
            "rank": 1,
        }
    ]

    saved_path = scraper.save_curated_videos_json(
        videos=sample_videos,
        output_path=out_file,
        search_query="podcast ia",
        min_score=0.60,
    )

    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert "metadata" in data
    assert data["metadata"]["search_query"] == "podcast ia"
    assert data["metadata"]["total_curated_kept"] == 1
    assert data["metadata"]["min_score_threshold"] == 0.60
    assert len(data["curated_videos"]) == 1
    assert data["curated_videos"][0]["video_id"] == "ex123"
