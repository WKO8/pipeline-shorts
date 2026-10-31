from __future__ import annotations

import io
from unittest.mock import patch
from rich.console import Console

from src.console_ui import (
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
from src.models import ClipCandidate


def test_console_messages():
    string_io = io.StringIO()
    test_console = Console(file=string_io, force_terminal=False, color_system=None, width=200)

    with patch("src.console_ui.console", test_console):
        print_header()
        print_stage("1. Obtenção de Mídia")
        print_success("Download", "Mídia baixada com sucesso!", elapsed=2.45)
        print_success("Whisper", "Transcrição finalizada")
        print_warning("Curadoria", "Poucos candidatos encontrados")
        print_error("Pipeline", "Falha de execução")
        print_thread(1, "Baixando trecho")

    output = string_io.getvalue()
    assert "Pipeline Avançado de Shorts" in output
    assert "1. Obtenção de Mídia" in output
    assert "✔ [Download]" in output
    assert "2.45s" in output
    assert "⚠ [Curadoria]" in output
    assert "✖ [Pipeline]" in output
    assert "🚀 [Thread 1]" in output


def test_cuts_table_dict_and_models():
    string_io = io.StringIO()
    test_console = Console(file=string_io, force_terminal=False, color_system=None, width=200)

    cuts_dict = [
        {
            "cut_id": "1",
            "start_second": 10.0,
            "end_second": 70.0,
            "duration_seconds": 60.0,
            "combined_score": 0.95,
            "video_file": "/tmp/corte_01.mp4",
        }
    ]

    clip_model = ClipCandidate(
        start=120.0,
        end=180.0,
        score=0.88,
        title="momento_viral",
    )

    from src.renderer import RenderResult
    render_result = RenderResult(
        clip=ClipCandidate(start=45.0, end=95.0, score=0.91, title="corte_renderizado"),
        output_path="/tmp/corte_renderizado.mp4",
    )

    with patch("src.console_ui.console", test_console):
        print_cuts_table(cuts_dict)
        print_cuts_table([clip_model])
        print_cuts_table([render_result])

    output = string_io.getvalue()
    assert "Cortes Selecionados para Processamento" in output
    assert "00:10" in output
    assert "01:10" in output
    assert "corte_01.mp4" in output
    assert "02:00" in output
    assert "03:00" in output
    assert "momento_viral.mp4" in output
    assert "00:45" in output
    assert "01:35" in output
    assert "50.0s" in output
    assert "corte_renderizado.mp4" in output


def test_cuts_table_with_source_spans():
    from src.models import SourceSpan
    from src.renderer import RenderResult

    string_io = io.StringIO()
    test_console = Console(file=string_io, force_terminal=False, color_system=None, width=200)

    # RenderResult where clip starts at 0.0s (relative to downloaded range),
    # but source_spans has the original video interval (1260s to 1338.6s -> 21:00 to 22:18)
    render_result = RenderResult(
        clip=ClipCandidate(
            start=0.0,
            end=78.6,
            score=0.85,
            title="corte_final",
            source_spans=[SourceSpan(start=1260.0, end=1338.6, role="full_cut")],
        ),
        output_path="/tmp/corte_final.mp4",
    )

    with patch("src.console_ui.console", test_console):
        print_cuts_table([render_result])

    output = string_io.getvalue()
    # 1260s = 21:00, 1338.6s = 22:18
    assert "21:00" in output
    assert "22:18" in output
    assert "78.6s" in output
    assert "corte_final.mp4" in output


def test_discovered_videos_table():
    string_io = io.StringIO()
    test_console = Console(file=string_io, force_terminal=False, color_system=None, width=200)

    videos = [
        {
            "rank": 1,
            "jev_score": 0.92,
            "jev_viral_score": 0.90,
            "jev_topic_relevance": 0.94,
            "duration_formatted": "1h 15m",
            "title": "Podcast Especial IA",
            "channel": "Tech Channel",
            "url": "https://youtube.com/watch?v=abc1234",
        }
    ]

    with patch("src.console_ui.console", test_console):
        print_discovered_videos_table(videos)

    output = string_io.getvalue()
    assert "Vídeos Longos Descobertos e Ranqueados pelo Jev" in output
    assert "#01" in output
    assert "0.920" in output
    assert "Podcast Especial IA" in output


def test_timing_table():
    string_io = io.StringIO()
    test_console = Console(file=string_io, force_terminal=False, color_system=None, width=200)

    timings = {
        "1. Obtenção de Mídia": 3.5,
        "2. Transcrição (Whisper)": 15.2,
        "3. Curadoria Cognitiva": 4.1,
        "4. Renderização e Tracking": 25.8,
    }

    with patch("src.console_ui.console", test_console):
        print_timing_table(timings, total_time=48.6)

    output = string_io.getvalue()
    assert "Tempos de Execução por Etapa" in output
    assert "Duração (s)" in output
    assert "1. Obtenção de Mídia" in output
    assert "3.50s" in output
    assert "Tempo Total" in output
    assert "48.60s" in output
