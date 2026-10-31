"""Presets de edição da Vó Sem Filtro.

Os valores abaixo foram lidos dos projetos do Shotcut do usuário (autosaves em
``~/.var/app/org.shotcut.Shotcut/data/Meltytech/Shotcut/autosave/*.mlt``) e são
reproduzidos literalmente no ``.mlt`` gerado, de forma que o render saia igual ao
export manual feito no Shotcut.

Dois estilos de gancho foram observados nos vídeos já publicados:

* ``antón``  — Text:Simple (dynamictext), fonte Anton, texto grande na altura do
  peito (é o preset que estava aberto no Shotcut).
* ``painel`` — texto em caixa preta no topo do quadro (usado no vídeo de react).

Só ``anton`` está calibrado; ``painel`` é um ponto de partida a calibrar com o export
real do usuário.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

# --- perfil de exportação (idêntico ao dos vídeos exportados no Shotcut) -------
WIDTH = 1080
HEIGHT = 1920
FPS = 60
VIDEO_BITRATE = "10M"
AUDIO_BITRATE = "256k"


@dataclass(frozen=True)
class PresetGancho:
    """Preset do gancho (filtro Text:Simple / dynamictext).

    A caixa fica na **faixa livre** entre o rosto (≈y 245–735) e a legenda (y 841–1440):
    com o gancho em 559+621 o texto termina em ~1180 e nada se sobrepõe. O preset
    original do Shotcut terminava em 1518 e cobria a legenda.
    """

    font: str = "Anton"
    size: int = 173            # "size" em px no .mlt (pointSize 130 na UI do Shotcut)
    point_size: int = 130
    weight: int = 400
    fgcolour: str = "#ffffffff"
    outline_colour: str = "#aa000000"
    outline: int = 35
    geometry: str = "-11 559 1080 621 1"
    halign: str = "center"
    valign: str = "bottom"
    anim_in: str = "00:00:00.000"


@dataclass(frozen=True)
class PresetLegenda:
    """Preset das legendas (filtro Subtitles)."""

    font: str = "Montserrat Black"
    size: int = 105            # "size" em px no .mlt (pointSize 79 na UI do Shotcut)
    point_size: int = 79
    weight: int = 900
    fgcolour: str = "#ffffffff"
    outline_colour: str = "#aa000000"
    outline: int = 20
    geometry: str = "5 841 1071 599 1"
    halign: str = "center"
    valign: str = "bottom"


@dataclass(frozen=True)
class Presets:
    gancho: PresetGancho = PresetGancho()
    legenda: PresetLegenda = PresetLegenda()
    hook_dur: float = 2.4          # janela do gancho, em segundos
    silence_db: int = -35          # limiar do silencedetect
    silence_min: float = 0.3       # duração mínima do silêncio inicial
    silence_keep: float = 0.10     # respiro mantido antes da fala

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Presets":
        """Sobrepõe apenas as chaves informadas (permite calibrar sem alterar o código)."""
        if not raw:
            return cls()
        base = cls()
        gancho = replace(base.gancho, **raw.get("gancho", {}))
        legenda = replace(base.legenda, **raw.get("legenda", {}))
        escalares = {k: v for k, v in raw.items() if k in {"hook_dur", "silence_db",
                                                           "silence_min", "silence_keep"}}
        return replace(base, gancho=gancho, legenda=legenda, **escalares)


# Presets alternativos, selecionáveis no spec por nome.
PRESETS = {
    "anton": Presets(),
    "painel": Presets(gancho=PresetGancho(
        font="Montserrat Black", size=92, point_size=69, weight=900,
        outline=14, geometry="0 40 1080 460 1")),
}
