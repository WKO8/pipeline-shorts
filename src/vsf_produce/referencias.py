"""Assets padrão da Vó Zulmira (folha de personagem e folha de cenário).

A pasta do personagem é a fonte da verdade visual do canal:

    ~/Pictures/A VoSemFiltro(Vo-Zulmira)-Personagem-IA/
    ├── character-sheet.png     (== vo-sem-filtro-character-sheet-2.png)
    ├── scenario-sheet.png
    ├── full-body.png
    ├── close-up-rosto.png
    └── ...

Esses arquivos entram na geração como **referências de imagem** (``gflow video r2v
--ref``), que é o mecanismo de upload de arquivo — diferente das ``@menções``, que não
funcionam nas contas servidas por ``flow.google.com``. É assim que a mesma Vó e o mesmo
cenário se repetem em todos os vídeos.
"""

from __future__ import annotations

import os
from pathlib import Path

PASTA_PADRAO = Path.home() / "Pictures" / "A VoSemFiltro(Vo-Zulmira)-Personagem-IA"

# Nomes canônicos dentro da pasta do personagem.
FOLHA_PERSONAGEM = "character-sheet.png"
FOLHA_CENARIO = "scenario-sheet.png"
CORPO_INTEIRO = "full-body.png"
CLOSE_ROSTO = "close-up-rosto.png"

# Ordem usada por padrão na geração (o omni-flash aceita até 7 referências).
PADRAO = (FOLHA_PERSONAGEM, FOLHA_CENARIO)


class AssetAusente(FileNotFoundError):
    """Referência padrão não encontrada no disco."""


def pasta(caminho: str | Path | None = None) -> Path:
    """Pasta dos assets (``VSF_ASSETS_DIR`` sobrepõe o padrão)."""
    return Path(caminho or os.environ.get("VSF_ASSETS_DIR", PASTA_PADRAO)).expanduser()


def caminho(nome: str, base: str | Path | None = None) -> Path:
    arquivo = pasta(base) / nome
    if not arquivo.exists():
        raise AssetAusente(
            f"asset padrão não encontrado: {arquivo}. Ajuste VSF_ASSETS_DIR ou o nome "
            "em src/vsf_produce/referencias.py.")
    return arquivo


def referencias_padrao(base: str | Path | None = None,
                       nomes: tuple[str, ...] = PADRAO) -> list[Path]:
    """Folha de personagem + folha de cenário, absolutas (obrigatórias para o gflow)."""
    return [caminho(nome, base) for nome in nomes]


def disponiveis(base: str | Path | None = None) -> dict[str, str]:
    """Inventário do que existe na pasta (para diagnóstico e para a página local)."""
    raiz = pasta(base)
    if not raiz.exists():
        return {}
    return {arquivo.name: str(arquivo)
            for arquivo in sorted(raiz.glob("*"))
            if arquivo.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}}
