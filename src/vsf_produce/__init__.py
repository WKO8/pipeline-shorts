"""Produção automática de Shorts da Vó Sem Filtro.

Fluxo: roteiro/prompt -> geração no Google Flow -> aprovação dos takes -> edição
(corte de silêncio, gancho e legendas com os presets do Shotcut) -> publicação.
"""

from __future__ import annotations

from .editor_mlt import ErroDeRender, Projeto, SemServidorGrafico, montar_projeto, render
from .presets import FPS, HEIGHT, WIDTH, PRESETS, Presets

__all__ = [
    "ErroDeRender", "FPS", "HEIGHT", "PRESETS", "Presets", "Projeto",
    "SemServidorGrafico", "WIDTH", "montar_projeto", "render",
]
