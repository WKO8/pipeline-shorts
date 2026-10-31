"""Roteiro de um Short da Vó Sem Filtro e o estado de aprovação dos takes.

Um roteiro é um JSON em ``data/vsf_produce/roteiros/<slug>.json``:

* o texto do gancho (o que aparece na tela, não o título do vídeo — os dois divergem
  muito neste canal);
* o prompt que vai para o Google Flow;
* a legenda (caminho de um SRT; se não houver, o pipeline transcreve o take);
* o histórico de takes gerados e qual foi aprovado.

O estado de aprovação vive no próprio JSON, então nada depende de banco: revisar é
abrir o arquivo ou a página local, e aprovar/regerar é uma escrita atômica.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .presets import PRESETS, Presets

BASE_PADRAO = Path("data/vsf_produce")
SAIDA_PADRAO = Path.home() / "Videos" / "VoSemFiltro" / "Edited-Videos"


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Take:
    """Um clipe bruto gerado (Flow) ou trazido à mão."""

    arquivo: str                       # caminho relativo à pasta do roteiro
    status: str = "pendente"           # pendente | aprovado | rejeitado
    criado_em: str = field(default_factory=_agora)
    origem: str = "flow"               # flow | manual
    modelo: str = ""
    motivos: list[str] = field(default_factory=list)   # pedidos de regeração

    @property
    def rejeitado(self) -> bool:
        return self.status == "rejeitado"


@dataclass
class Roteiro:
    slug: str
    semana: int
    gancho: str
    prompt: str
    titulo: str = ""
    descricao: str = ""
    tags: list[str] = field(default_factory=list)
    legenda_srt: str | None = None
    preset: str = "anton"
    gancho_segundos: float = 2.4
    modelo: str = "veo-3.1-fast"
    duracao: int = 8
    aspect: str = "9:16"
    resolucao: str = "720p"
    cena: str = ""
    expressao: str = ""
    acao: str = ""
    referencia_quadro: str | None = None
    prompt_template: str | None = None
    usar_referencias_padrao: bool = True
    referencias: list[str] = field(default_factory=list)
    personagem: str | None = None
    presets: dict = field(default_factory=dict)
    takes: list[Take] = field(default_factory=list)
    aprovado: str | None = None

    # ---- presets -----------------------------------------------------------
    def presets_efetivos(self) -> Presets:
        """Preset nomeado (`.preset`) com os ajustes finos do roteiro por cima."""
        base = PRESETS.get(self.preset, PRESETS["anton"])
        ajustes = dict(self.presets)
        ajustes.setdefault("hook_dur", self.gancho_segundos)
        return Presets.from_dict(_mesclar(asdict(base), ajustes))

    # ---- takes -------------------------------------------------------------
    def aprovado_take(self) -> Take | None:
        return next((t for t in self.takes if t.arquivo == self.aprovado), None)

    def pendentes(self) -> list[Take]:
        return [t for t in self.takes if t.status == "pendente"]

    def aprovar(self, arquivo: str) -> Take:
        take = _achar(self, arquivo)
        for t in self.takes:
            if t.status == "aprovado" and t is not take:
                t.status = "rejeitado"
                t.motivos.append("substituído por outro take aprovado")
        take.status = "aprovado"
        self.aprovado = take.arquivo
        return take

    def rejeitar(self, arquivo: str, motivo: str = "") -> Take:
        take = _achar(self, arquivo)
        take.status = "rejeitado"
        if motivo:
            take.motivos.append(motivo)
        if self.aprovado == take.arquivo:
            self.aprovado = None
        return take

    # ---- saída -------------------------------------------------------------
    def nome_saida(self) -> str:
        return f"vosemfiltro-semana{self.semana}-{self.slug}.mp4"

    def pasta_saida(self, raiz: Path | None = None) -> Path:
        raiz = raiz or Path(os.environ.get("VSF_OUTPUT_DIR", SAIDA_PADRAO))
        return Path(raiz) / f"Semana{self.semana}"


def _achar(roteiro: Roteiro, arquivo: str) -> Take:
    for take in roteiro.takes:
        if take.arquivo == arquivo or Path(take.arquivo).name == Path(arquivo).name:
            return take
    raise KeyError(f"take não encontrado no roteiro: {arquivo}")


def _mesclar(base: dict, ajustes: dict) -> dict:
    """Mescla recursiva: ajustes sobrepõem apenas o que informam."""
    saida = dict(base)
    for chave, valor in ajustes.items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = {**saida[chave], **valor}
        else:
            saida[chave] = valor
    return saida


# --------------------------------------------------------------------------- IO

def pasta_do_roteiro(roteiro: Roteiro, base: Path | None = None) -> Path:
    base = Path(base or BASE_PADRAO)
    return base / "roteiros" / roteiro.slug


def caminho_do_roteiro(slug: str, base: Path | None = None) -> Path:
    return Path(base or BASE_PADRAO) / "roteiros" / f"{slug}.json"


def salvar(roteiro: Roteiro, base: Path | None = None) -> Path:
    destino = caminho_do_roteiro(roteiro.slug, base)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".json.tmp")
    dados = asdict(roteiro)
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    temporario.replace(destino)          # escrita atômica: nunca deixa JSON pela metade
    return destino


def carregar(caminho: Path | str) -> Roteiro:
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    takes = [Take(**t) for t in dados.pop("takes", [])]
    return Roteiro(takes=takes, **dados)


def listar(base: Path | None = None) -> list[Roteiro]:
    pasta = Path(base or BASE_PADRAO) / "roteiros"
    if not pasta.exists():
        return []
    return [carregar(p) for p in sorted(pasta.glob("*.json"))]


def novo(base: Path | None = None, *, slug: str, semana: int, gancho: str, prompt: str,
         **campos) -> Roteiro:
    """Cria (e salva) um roteiro novo, recusando slug já existente."""
    if caminho_do_roteiro(slug, base).exists():
        raise FileExistsError(f"já existe roteiro com slug {slug!r}")
    roteiro = Roteiro(slug=slug, semana=semana, gancho=gancho, prompt=prompt, **campos)
    salvar(roteiro, base)
    return roteiro


def srt_do_roteiro(roteiro: Roteiro, base: Path | None = None) -> Path | None:
    if not roteiro.legenda_srt:
        return None
    caminho = Path(roteiro.legenda_srt)
    if not caminho.is_absolute():
        caminho = pasta_do_roteiro(roteiro, base) / caminho
    return caminho if caminho.exists() else None
