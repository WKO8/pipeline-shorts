from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.jev_curator import JevTwoLayerCurator


class FakeNoulAnswer:
    def __init__(self, val: float):
        self.noul = val


class FakeSystemOneResponse:
    def __init__(self, answers: dict):
        self.answers = answers


class FakeTypeSafeClient:
    def __init__(self, hook_val: float = 0.88, narr_val: float = 0.92):
        self.hook_val = hook_val
        self.narr_val = narr_val

    async def system_one(self, state: str, model: str, questions: dict):
        answers = {}
        if "hook_interest" in questions:
            answers["hook_interest"] = FakeNoulAnswer(self.hook_val)
        if "narrative_quality" in questions:
            answers["narrative_quality"] = FakeNoulAnswer(self.narr_val)
        return FakeSystemOneResponse(answers)


@pytest.mark.anyio
async def test_curate_hooks_80_percent_reduction():
    # 5 blocks of 60s -> duration 300s
    segments = [
        {"start": float(i * 10), "end": float((i + 1) * 10), "text": f"Fala bloco {i}"}
        for i in range(30)
    ]

    fake_client = FakeTypeSafeClient(hook_val=0.75)
    curator = JevTwoLayerCurator(api_key="key", client=fake_client)

    # 300s / 60s = 5 raw hooks
    # keep_ratio 0.20 of 5 = 1 hook kept
    hooks = await curator.curate_hooks(segments, window_size=60.0, keep_ratio=0.20, min_score=0.60)
    assert len(hooks) == 1
    assert hooks[0]["hook_score"] == 0.75


def test_find_natural_sentence_end():
    segments = [
        {"start": 0.0, "end": 50.0, "text": "Início da história"},
        {"start": 50.0, "end": 170.0, "text": "Desenvolvimento"},
        {"start": 170.0, "end": 200.0, "text": "E aí aconteceu isso"},
        {"start": 200.0, "end": 220.0, "text": "Essa foi a grande lição."},  # Has '.' in 180s-300s window
        {"start": 220.0, "end": 290.0, "text": "Continuando a conversa"},
    ]

    end_s, text = JevTwoLayerCurator._find_natural_sentence_end(
        start_sec=0.0,
        segments=segments,
        min_duration=180.0,
        max_duration=300.0,
    )
    assert end_s == 220.0
    assert "Essa foi a grande lição." in text


def test_find_natural_sentence_end_short_form_with_closure():
    segments = [
        {"start": 10.0, "end": 22.0, "text": "Vocês não vão acreditar no que o cliente pediu."},
        {"start": 22.5, "end": 35.0, "text": "Ele queria dobrar as vendas sem gastar um centavo."},
        {"start": 35.4, "end": 48.0, "text": "E foi assim que faturamos o primeiro milhão!"},  # Punchline with closure + ! (duration 38s)
        {"start": 48.8, "end": 65.0, "text": "Mas mudando totalmente de assunto, no outro dia..."},
        {"start": 65.0, "end": 185.0, "text": "Continuamos conversando sobre outros tópicos aleatórios."},
    ]

    end_s, text = JevTwoLayerCurator._find_natural_sentence_end(
        start_sec=10.0,
        segments=segments,
        min_duration=20.0,
        max_duration=180.0,
    )
    assert end_s == 48.0
    assert "E foi assim que faturamos o primeiro milhão!" in text
    assert "mudando totalmente de assunto" not in text


def test_find_natural_sentence_end_avoids_continuation_and_questions():
    segments = [
        {"start": 10.0, "end": 25.0, "text": "Eu comecei pagando R$ 3.000 para dar uma palestra."},
        {"start": 25.5, "end": 40.0, "text": "Falei: quanto daria isso? Ele 12.000."},  # Ends with '.', BUT next starts with 'Aí...'
        {"start": 40.2, "end": 55.0, "text": "Aí no próximo convite eu cobrei 15.000 e fui subindo."},
        {"start": 55.5, "end": 65.0, "text": "E aí, hoje você tá entendendo que o YouTube é essa porta?"},  # Question! Should NOT be cut end
        {"start": 65.2, "end": 78.0, "text": "Hoje eu cobro 200 mil e acabo gerando lead pro meu negócio."},  # Punchline with closure + topic shift next
        {"start": 78.5, "end": 90.0, "text": ">> Cada time desse tem quantas pessoas?"},  # New topic / question
    ]

    end_s, text = JevTwoLayerCurator._find_natural_sentence_end(
        start_sec=10.0,
        segments=segments,
        min_duration=20.0,
        max_duration=180.0,
    )
    # Must NOT cut at 40.0s (mid-continuation) nor at 65.0s (question), must cut at 78.0s (closure)
    assert end_s == 78.0
    assert "lead pro meu negócio." in text
    assert "Cada time desse tem quantas pessoas?" not in text


def test_deduplicate_narrative_windows():
    curator = JevTwoLayerCurator(api_key="key")

    candidates = [
        {
            "hook_id": 1,
            "start_second": 100.0,
            "end_second": 300.0,  # 200s duration
            "hook_score": 0.90,  # Higher hook score
        },
        {
            "hook_id": 2,
            "start_second": 120.0,
            "end_second": 310.0,  # Highly overlapping with candidate 1 (intersection 180s / 190s = 94%)
            "hook_score": 0.70,  # Lower hook score -> should be discarded
        },
        {
            "hook_id": 3,
            "start_second": 500.0,
            "end_second": 720.0,  # No overlap -> should be kept
            "hook_score": 0.80,
        },
    ]

    deduped = curator._deduplicate_narrative_windows(candidates, max_overlap=0.50)
    assert len(deduped) == 2
    assert deduped[0]["hook_id"] == 1
    assert deduped[1]["hook_id"] == 3


@pytest.mark.anyio
async def test_expand_and_evaluate_narratives_and_combined_score():
    fake_client = FakeTypeSafeClient(hook_val=0.88, narr_val=0.92)
    curator = JevTwoLayerCurator(api_key="key", client=fake_client)

    top_hooks = [
        {
            "hook_id": 1,
            "start_second": 600.0,
            "end_second": 660.0,
            "hook_score": 0.88,
        }
    ]

    segments = [
        {"start": 600.0, "end": 700.0, "text": "Começo do gancho"},
        {"start": 700.0, "end": 797.8, "text": "Conclusão definitiva da história!"},
        {"start": 797.8, "end": 900.0, "text": "Outro assunto"},
    ]

    cuts = await curator.expand_and_evaluate_narratives(
        top_hooks=top_hooks,
        segments=segments,
        min_duration=180.0,
        max_duration=300.0,
    )

    assert len(cuts) == 1
    cut = cuts[0]
    assert cut["cut_id"] == 1
    assert cut["start_second"] == 600.0
    assert cut["end_second"] == 797.8
    assert cut["hook_score"] == 0.88
    assert cut["narrative_score"] == 0.92
    # Combined = 0.40 * 0.88 + 0.60 * 0.92 = 0.352 + 0.552 = 0.904
    assert cut["combined_score"] == 0.904


def test_save_viral_cuts_json(tmp_path: Path):
    curator = JevTwoLayerCurator(api_key="key")
    out_file = tmp_path / "viral_cuts_UF8uR6Z6KLc.json"

    cuts = [
        {
            "cut_id": 1,
            "start_second": 600.0,
            "end_second": 797.8,
            "duration_seconds": 197.8,
            "hook_score": 0.88,
            "narrative_score": 0.92,
            "combined_score": 0.904,
            "full_transcript": "Texto do corte...",
        }
    ]

    saved_path = curator.save_viral_cuts_json(
        cuts=cuts,
        output_path=out_file,
        video_id="UF8uR6Z6KLc",
        title="Steve Jobs Stanford",
    )

    assert saved_path.exists()
    data = json.loads(saved_path.read_text(encoding="utf-8"))
    assert data["metadata"]["video_id"] == "UF8uR6Z6KLc"
    assert data["metadata"]["total_cuts_found"] == 1
    assert len(data["viral_cuts"]) == 1
    assert data["viral_cuts"][0]["combined_score"] == 0.904

    clip_candidates = curator.to_clip_candidates(cuts)
    assert len(clip_candidates) == 1
    assert clip_candidates[0].start == 600.0
    assert clip_candidates[0].end == 797.8
    assert clip_candidates[0].score == 0.904


@pytest.mark.anyio
async def test_curate_hooks_retains_multiple_when_scores_below_high_threshold():
    # 20 blocks of 60s -> duration 1200s (20 hooks)
    segments = [
        {"start": float(i * 60), "end": float((i + 1) * 60), "text": f"Fala bloco {i}."}
        for i in range(20)
    ]

    # Scores around 0.45-0.55 (conversational podcasts, below 0.60)
    fake_client = FakeTypeSafeClient(hook_val=0.48)
    curator = JevTwoLayerCurator(api_key="key", client=fake_client)

    # Even with min_score=0.60, min_keep guarantees retaining top candidates instead of collapsing to 1
    hooks = await curator.curate_hooks(
        segments,
        window_size=60.0,
        keep_ratio=0.20,
        min_score=0.60,
        min_keep=4,
    )
    assert len(hooks) == 4
    for h in hooks:
        assert h["hook_score"] == 0.48


@pytest.mark.anyio
async def test_curate_respects_max_clips():
    fake_client = FakeTypeSafeClient(hook_val=0.75, narr_val=0.80)
    curator = JevTwoLayerCurator(api_key="key", client=fake_client)

    # 10 non-overlapping blocks of 300s
    segments = []
    for i in range(10):
        start = float(i * 400)
        segments.append({"start": start, "end": start + 200.0, "text": f"Bloco {i} história completa."})
        segments.append({"start": start + 200.0, "end": start + 300.0, "text": "Fim do assunto."})

    cuts = await curator.curate(
        segments=segments,
        min_duration=180.0,
        max_duration=300.0,
        max_clips=2,
    )
    assert len(cuts) == 2
    assert cuts[0]["cut_id"] == 1
    assert cuts[1]["cut_id"] == 2

