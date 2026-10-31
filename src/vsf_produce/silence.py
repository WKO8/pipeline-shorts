"""Detecção do silêncio inicial do clipe bruto do Flow.

Os clipes gerados no Flow começam com 0,6–1,0 s de silêncio (medido em
``Semana 1/v1.mp4`` = 1,00 s e ``Semana 2/v1.mp4`` = 0,61 s). O corte é feito pelo
ponto ``in`` do produtor no ``.mlt`` — não há re-encode prévio.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FFMPEG = "ffmpeg"


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.stderr


def leading_silence(path: Path, *, noise_db: int = -35, min_seconds: float = 0.3,
                    keep: float = 0.10, ffmpeg: str = FFMPEG) -> float:
    """Devolve quantos segundos devem ser cortados do começo do clipe.

    Só conta silêncio que **começa no quadro zero**; uma pausa no meio da fala não
    altera o corte. `keep` preserva um respiro antes da primeira palavra, para o
    corte não soar abrupto.
    """
    log = _run([ffmpeg, "-hide_banner", "-i", str(path), "-af",
                f"silencedetect=noise={noise_db}dB:d={min_seconds}", "-f", "null", "-"])
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", log)]
    if not starts or starts[0] > 0.05 or not ends:
        return 0.0
    return round(max(ends[0] - keep, 0.0), 3)


def duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """Duração do clipe em segundos."""
    proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0
