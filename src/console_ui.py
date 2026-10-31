from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Global singleton console
console = Console()


def print_header() -> None:
    """Renders the standard pipeline banner header."""
    console.print(
        Panel.fit(
            "[bold magenta]⚡ Pipeline Avançado de Shorts & Tracking Facial[/bold magenta]\n"
            "[cyan]Detecção de Cortes + Auto-Reframe 9:16 + Legendas Dinâmicas[/cyan]",
            border_style="magenta",
        )
    )


def print_stage(title: str) -> None:
    """Prints a stage header with standardized emoji and styling."""
    console.print(f"\n[bold yellow]🎬 {title}[/bold yellow]")


def print_success(module: str, msg: str, elapsed: Optional[float] = None) -> None:
    """Prints a success message with green checkmark."""
    if elapsed is not None:
        console.print(f"  [bold green]✔ [{module}][/bold green] {msg} em [bold cyan]{elapsed:.2f}s[/bold cyan]!")
    else:
        console.print(f"  [bold green]✔ [{module}][/bold green] {msg}")


def print_warning(module: str, msg: str) -> None:
    """Prints a warning message with yellow alert icon."""
    console.print(f"  [bold yellow]⚠ [{module}][/bold yellow] {msg}")


def print_error(module: str, msg: str) -> None:
    """Prints an error message with red cross icon."""
    console.print(f"  [bold red]✖ [{module}][/bold red] {msg}")


def print_thread(thread_id: Any, msg: str) -> None:
    """Prints parallel/thread processing message."""
    console.print(f"  [cyan]🚀 [Thread {thread_id}][/cyan] {msg}")


def print_cuts_table(
    cortes: List[Union[Dict[str, Any], Any]],
    title: str = "🔥 Cortes Selecionados para Processamento",
) -> None:
    """Renders a rich table of selected/curated video cuts."""
    table = Table(title=title, border_style="cyan")
    table.add_column("Corte #", justify="center", style="bold yellow")
    table.add_column("Início", justify="center", style="white")
    table.add_column("Fim", justify="center", style="white")
    table.add_column("Duração", justify="center", style="cyan")
    table.add_column("Score", justify="right", style="bold green")
    table.add_column("Arquivo 9:16", style="dim")

    for idx, item in enumerate(cortes, start=1):
        if hasattr(item, "to_dict"):
            data = item.to_dict()
        elif isinstance(item, dict):
            data = item
        else:
            data = getattr(item, "__dict__", {})

        # Se for um RenderResult, os dados do corte estão aninhados em 'clip'
        clip_data = data.get("clip")
        if hasattr(clip_data, "to_dict"):
            clip_data = clip_data.to_dict()
        if isinstance(clip_data, dict):
            source_spans = clip_data.get("source_spans") or []
            if source_spans and clip_data.get("start", 0.0) == 0.0:
                first_span = source_spans[0]
                span_st = first_span.get("start") if isinstance(first_span, dict) else getattr(first_span, "start", None)
                span_en = first_span.get("end") if isinstance(first_span, dict) else getattr(first_span, "end", None)
                if span_st is not None and span_en is not None:
                    data["start_second"] = span_st
                    data["end_second"] = span_en
            for k in ["start", "end", "duration", "score", "title"]:
                if k in clip_data and data.get(k) is None:
                    data[k] = clip_data[k]

        c_id = str(data.get("cut_id") or data.get("id") or idx)
        start_s = float(data.get("start_second") if data.get("start_second") is not None else data.get("start", 0.0))
        end_s = float(data.get("end_second") if data.get("end_second") is not None else data.get("end", 0.0))
        dur = float(data.get("duration_seconds") if data.get("duration_seconds") is not None else data.get("duration", (end_s - start_s)))
        score = float(data.get("combined_score") if data.get("combined_score") is not None else data.get("score", 0.0))
        
        raw_out = data.get("video_file") or data.get("output_path") or data.get("output_file")
        if raw_out:
            from pathlib import Path
            out_f = Path(raw_out).name
        elif data.get("title"):
            title_txt = str(data["title"])
            out_f = title_txt if title_txt.endswith(".mp4") else f"{title_txt}.mp4"
        else:
            out_f = f"corte_{c_id}.mp4"

        start_fmt = f"{int(start_s // 60):02d}:{int(start_s % 60):02d}"
        end_fmt = f"{int(end_s // 60):02d}:{int(end_s % 60):02d}"

        table.add_row(
            f"#{c_id}",
            start_fmt,
            end_fmt,
            f"{dur:.1f}s",
            f"{score:.3f}",
            out_f,
        )

    console.print("\n", table)


def print_discovered_videos_table(videos: List[Dict[str, Any]]) -> None:
    """Renders a rich table of discovered long videos ranked by Jev."""
    table = Table(title="🔍 Vídeos Longos Descobertos e Ranqueados pelo Jev", border_style="magenta")
    table.add_column("Rank", justify="center", style="bold yellow")
    table.add_column("Score Jev", justify="right", style="bold green")
    table.add_column("Viral", justify="center", style="cyan")
    table.add_column("Tema", justify="center", style="cyan")
    table.add_column("Duração", justify="center", style="white")
    table.add_column("Título", style="bold")
    table.add_column("Canal", style="dim")
    table.add_column("URL", style="blue underline")

    for v in videos:
        rank_str = f"#{v.get('rank', 1):02d}"
        score_str = f"{v.get('jev_score', 0.0):.3f}"
        viral_str = f"{v.get('jev_viral_score', 0.0):.2f}"
        rel = v.get("jev_topic_relevance")
        rel_str = f"{rel:.2f}" if rel is not None else "-"
        dur_str = v.get("duration_formatted") or f"{v.get('duration_seconds', 0)}s"
        title_str = (v.get("title") or "")[:45]
        channel_str = (v.get("channel") or "")[:20]
        url_str = v.get("url") or ""

        table.add_row(
            rank_str, score_str, viral_str, rel_str, dur_str, title_str, channel_str, url_str
        )

    console.print("\n", table)


def print_timing_table(timings: Dict[str, float], total_time: float) -> None:
    """Renders execution benchmark timing table."""
    timing_table = Table(title="⏱️ Tempos de Execução por Etapa", border_style="yellow")
    timing_table.add_column("Etapa do Pipeline", style="cyan")
    timing_table.add_column("Duração (s)", justify="right", style="bold yellow")

    for step_name, step_time in timings.items():
        timing_table.add_row(step_name, f"{step_time:.2f}s")
    timing_table.add_row("Tempo Total", f"{total_time:.2f}s", style="bold green")

    console.print("\n", timing_table)
