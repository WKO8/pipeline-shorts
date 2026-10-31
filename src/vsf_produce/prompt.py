"""Prompt base da Vó Sem Filtro: monta o texto que vai para o Flow.

O arquivo ``prompts/vsf-prompt-base.txt`` é o template do usuário, com blocos
delimitados por ``== Nome ==``:

* ``BLOCO A``     — a descrição fixa em inglês (personagem, cenário, voz, enquadramento);
* ``Acao``        — ``[BLOCO A] [ACAO]``: o prompt de vídeo é o bloco A + a ação daquele
  vídeo (a fala/o gesto). É a forma operacional dos roteiros A1–A15 do canal;
* ``Frame inicial`` / ``Thumbnail`` — blocos com ``[CENA]`` e ``[EXPRESSAO]`` para gerar as
  imagens (``gflow image``);
* ``Referencias`` — as ``@tags`` de ativo do Flow (Character, folha, quadro inicial).
  **Não funcionam** em contas servidas por ``flow.google.com`` (o gflow aborta com
  ``MentionIndexUnavailableError``); o padrão do pipeline usa a folha de personagem e a
  de cenário como **arquivos de referência** (``r2v --ref``), que é upload de arquivo;
* ``Descricao`` / ``Hashtags`` — descrição padrão (``[LEGENDA]``/``[SET]``) e os conjuntos
  de hashtags A1/A2/A3.

Nomes antigos do template (``Initial Frame``, ``Thubnail``, ``Video``) continuam aceitos.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE_PADRAO = Path(__file__).parent / "prompts" / "vsf-prompt-base.txt"
PERSONAGEM_PADRAO = "Vó Zulmira"

# nome canônico -> aliases aceitos no template (inclui os nomes antigos do usuário)
BLOCOS = {
    "bloco_a": ("BLOCO A",),
    "acao": ("Acao", "Ação"),
    "frame": ("Frame inicial", "Initial Frame"),
    "thumbnail": ("Thumbnail", "Thubnail"),
    "referencias": ("Referencias", "Referências"),
    "descricao": ("Descricao", "Descrição"),
    "hashtags": ("Hashtags",),
    "video": ("Video",),
}


class PromptInvalido(ValueError):
    """Template sem os blocos esperados."""


def carregar_template(caminho: Path | str | None = None) -> str:
    caminho = Path(caminho) if caminho else TEMPLATE_PADRAO
    if not caminho.exists():
        raise PromptInvalido(f"template de prompt não encontrado: {caminho}")
    return caminho.read_text(encoding="utf-8")


def _bloco(template: str, *nomes: str) -> str:
    """Conteúdo de ``== <nome> ==`` até o próximo cabeçalho (aceita aliases)."""
    for nome in nomes:
        padrao = re.compile(rf"^==\s*{re.escape(nome)}\s*==\s*$(.*?)(?=^==|\Z)",
                            re.MULTILINE | re.DOTALL | re.IGNORECASE)
        achado = padrao.search(template)
        if achado:
            return achado.group(1).strip()
    raise PromptInvalido("o template não tem o bloco '== " + "' nem '== ".join(nomes) + " =='")


def _bloco_por_chave(template: str, chave: str) -> str:
    return _bloco(template, *BLOCOS[chave])


def _trocar(texto: str, marcador: str, valor: str) -> str:
    if not valor:
        return texto
    return re.sub(rf"\[\s*{re.escape(marcador)}\s*\]", valor, texto)


def _tirar_colchetes(texto: str) -> str:
    """``[@Vó Zulmira]`` -> ``@Vó Zulmira``.

    Os colchetes são a notação do template (marcam onde entra o ativo); o Flow resolve
    a tag ``@Nome``, não o texto entre colchetes.
    """
    return re.sub(r"\[\s*(@[^\[\]]+?)\s*\]", r"\1", texto)


# --------------------------------------------------------------------- blocos

def bloco_a(template: str | None = None) -> str:
    """A descrição fixa em inglês (personagem + cenário + voz + enquadramento)."""
    return _bloco_por_chave(carregar_template(template), "bloco_a")


def prompt_com_acao(acao: str, template: str | None = None) -> str:
    """Prompt do vídeo: **BLOCO A + a ação/fala** daquele roteiro.

    É a forma operacional do canal (``[BLOCO A] She holds the receiver … and says: …``).
    """
    if not acao.strip():
        raise PromptInvalido("informe a ação (a fala/gesto do vídeo)")
    texto = carregar_template(template)
    try:
        corpo = _bloco_por_chave(texto, "acao")
    except PromptInvalido:
        corpo = "[BLOCO A] [ACAO]"
    corpo = _trocar(corpo, "BLOCO A", _bloco_por_chave(texto, "bloco_a"))
    return _trocar(corpo, "ACAO", acao.strip()).strip()


def prompt_do_frame(cena: str, template: str | None = None) -> str:
    """Bloco do quadro inicial, com a cena do vídeo no lugar de ``[CENA]``."""
    if not cena.strip():
        raise PromptInvalido("informe a cena (é ela que varia em cada vídeo)")
    return _tirar_colchetes(
        _trocar(_bloco_por_chave(carregar_template(template), "frame"), "CENA",
                cena.strip()))


def prompt_da_thumbnail(expressao: str, template: str | None = None) -> str:
    """Bloco da thumbnail, com a expressão no lugar de ``[EXPRESSAO]``."""
    if not expressao.strip():
        raise PromptInvalido("informe a expressão da thumbnail")
    return _tirar_colchetes(
        _trocar(_bloco_por_chave(carregar_template(template), "thumbnail"), "EXPRESSAO",
                expressao.strip()))


def prompt_com_referencias(*, personagem: str = PERSONAGEM_PADRAO,
                           referencia_quadro: str | None = None,
                           template: str | None = None) -> str:
    """Bloco de referências (``@tags``) + BLOCO A — para quando as menções funcionarem."""
    texto = carregar_template(template)
    try:
        cabecalho = _bloco_por_chave(texto, "referencias")
    except PromptInvalido:
        cabecalho = ""
    nome = personagem.strip().lstrip("@")
    cabecalho = re.sub(r"@?Vó Zulmira", f"@{nome}", cabecalho)          # Character + Voice
    if referencia_quadro:
        cabecalho = re.sub(r"initial-frame-vX\.png", referencia_quadro.strip(), cabecalho)
    corpo = _bloco_por_chave(texto, "bloco_a")
    junto = f"{_tirar_colchetes(cabecalho)}\n\n{corpo}" if cabecalho else corpo
    return junto.strip()


def prompt_do_video(*, personagem: str = PERSONAGEM_PADRAO, referencia_quadro: str | None = None,
                    template: str | None = None) -> str:
    """Compatibilidade: bloco ``Video`` (legado) ou referências + BLOCO A."""
    texto = carregar_template(template)
    try:
        legado = _bloco_por_chave(texto, "video")
    except PromptInvalido:
        return prompt_com_referencias(personagem=personagem,
                                      referencia_quadro=referencia_quadro,
                                      template=template)
    nome = personagem.strip().lstrip("@")
    legado = re.sub(r"@?Vó Zulmira", f"@{nome}", legado)
    if referencia_quadro:
        legado = re.sub(r"initial-frame-vX\.png", referencia_quadro.strip(), legado)
    return _tirar_colchetes(legado).strip()


def sem_referencias(texto: str) -> str:
    """Remove o bloco ``References:`` (linhas com ``@tags``) do prompt.

    Necessário em contas servidas por ``flow.google.com``: o gflow não consegue carregar
    o catálogo de ativos e aborta com ``MentionIndexUnavailableError`` antes de enviar
    ("the character source is unavailable"). O personagem continua vindo do projeto e das
    imagens de referência — o que se perde é a tag explícita.
    """
    linhas = [linha for linha in texto.splitlines()
              if not linha.strip().startswith("@")
              and not re.match(r"^\s*(Character|Voice|Character Sheet|Initial Frame)\s*:",
                               linha)]
    limpo = re.sub(r"^\s*References:\s*$", "", "\n".join(linhas), flags=re.MULTILINE)
    return re.sub(r"\n{3,}", "\n\n", limpo).strip()


# ------------------------------------------------------------------ publicação

def sets_de_hashtags(template: str | None = None) -> dict[str, str]:
    """Conjuntos de hashtags do template: ``{"A1": "#conselhodavovo #vosemfiltro …", …}``."""
    corpo = _bloco_por_chave(carregar_template(template), "hashtags")
    conjuntos: dict[str, str] = {}
    for linha in corpo.splitlines():
        achado = re.match(r"^\s*-\s*([A-Za-z0-9]+)\s*(?:\([^)]*\))?\s*:\s*(.+)$", linha)
        if achado:
            conjuntos[achado.group(1).upper()] = achado.group(2).strip()
    return conjuntos


def descricao_padrao(legenda: str, hashtags: str, template: str | None = None) -> str:
    """Descrição padrão do post: legenda + CTA + rótulo de IA + conjunto de hashtags."""
    corpo = _bloco_por_chave(carregar_template(template), "descricao")
    corpo = _trocar(corpo, "LEGENDA", legenda.strip())
    corpo = _trocar(corpo, "SET", hashtags.strip())
    return re.sub(r"\n{3,}", "\n\n", corpo).strip()


def prompt_completo(*, cena: str = "", expressao: str = "", acao: str = "",
                    personagem: str = PERSONAGEM_PADRAO,
                    referencia_quadro: str | None = None,
                    template: str | None = None) -> str:
    """Conveniência: a ação vence; sem ação, cai no bloco com referências."""
    if acao.strip():
        return prompt_com_acao(acao, template)
    _ = (cena, expressao)
    return prompt_com_referencias(personagem=personagem,
                                  referencia_quadro=referencia_quadro, template=template)
