"""Driver de geração no Google Flow, em cima do `gflow-cli`.

O Flow não tem API pública: a geração exige cliques reais na interface (os endpoints
são protegidos por reCAPTCHA Enterprise e tokens programáticos recebem 403). O
`gflow-cli` dirige um Chromium de verdade autenticado na conta do usuário — portanto
**os créditos gastos são os da assinatura Google AI**, não cobrança por segundo.

Custo em créditos (tabela do Google) por geração:

===================  ==========================
modelo               créditos
===================  ==========================
veo-lite / veo-lite-lp   10 / 0 (só Ultra)
veo-fast                  20
veo-quality              100
omni-flash 720p         7/10/12/15 (4/6/8/10 s)
omni-flash 360p          4/5/6/7
===================  ==========================

O upscale para 1080p é **gratuito** para assinantes — por isso os clipes brutos de
720x1280 do canal viram 1080x1920 na edição sem custo extra.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .spec import Roteiro

# alias do gflow -> apelidos aceitos no roteiro
ALIASES = {
    "veo-fast": "veo-fast", "veo-3.1-fast": "veo-fast", "fast": "veo-fast",
    "veo-lite": "veo-lite", "veo-3.1-lite": "veo-lite", "lite": "veo-lite",
    "veo-quality": "veo-quality", "veo-3.1-quality": "veo-quality",
    "quality": "veo-quality",
    "veo-lite-lp": "veo-lite-lp", "veo-3.1-lite-low-priority": "veo-lite-lp",
    "omni-flash": "omni-flash", "omni": "omni-flash",
}

CUSTO = {
    "veo-lite": 10, "veo-fast": 20, "veo-quality": 100, "veo-lite-lp": 0,
    "omni-flash": {"720p": {4: 7, 6: 10, 8: 12, 10: 15},
                   "360p": {4: 4, 6: 5, 8: 6, 10: 7}},
}


class FlowIndisponivel(RuntimeError):
    """gflow-cli ausente ou sem sessão autenticada."""


class SaldoIndisponivel(RuntimeError):
    """A conta não expõe o saldo pela CLI (normal em contas servidas por flow.google.com)."""


class RoteiroInvalido(ValueError):
    """Combinação de modelo/duração/resolução que o Flow não aceita."""


def comando_gflow() -> list[str]:
    explicito = os.environ.get("VSF_GFLOW")
    if explicito:
        return explicito.split()
    caminho = shutil.which("gflow") or shutil.which("gflow", path=os.path.expanduser(
        "~/.local/bin"))
    if not caminho:
        raise FlowIndisponivel(
            "gflow não encontrado. Instale com `uv tool install gflow-cli` ou defina "
            "VSF_GFLOW.")
    return [caminho]


def _rodar(args: list[str], *, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([*comando_gflow(), *args], capture_output=True, text=True,
                          timeout=timeout)


def versao() -> str:
    try:
        return _rodar(["--version"], timeout=60).stdout.strip()
    except (FlowIndisponivel, subprocess.SubprocessError):
        return ""


def creditos(profile: str | None = None) -> dict:
    """Saldo atual (leitura pura: não gasta crédito).

    Em contas que o Google serve por ``flow.google.com`` a CLI **não recebe** o token
    que expõe o saldo (gflow #795) — a geração funciona, mas o número só aparece no
    próprio Flow. Nesse caso levanta ``SaldoIndisponivel`` em vez de fingir que a
    sessão morreu.
    """
    args = ["credits", "user", "--json"]
    if profile:
        args += ["--profile", profile]
    proc = _rodar(args, timeout=180)
    if proc.returncode != 0:
        saida = f"{proc.stdout}\n{proc.stderr}"
        if "AisandboxAuthError" in saida or "no access token" in saida:
            raise SaldoIndisponivel(
                "sua conta é servida por flow.google.com: a CLI não recebe o token do "
                "saldo (gflow #795). A geração funciona normalmente — confira os "
                "créditos na própria interface do Flow.")
        raise FlowIndisponivel(
            "não consegui ler os créditos — a sessão do Flow provavelmente expirou "
            f"(saída: {proc.stderr.strip()[:200] or proc.stdout.strip()[:200]}). "
            "Rode: gflow auth login --browser chrome")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"bruto": proc.stdout.strip()}


def sessao_ok(profile: str | None = None) -> bool:
    """True se o perfil tem sessão do Flow verificada.

    Usa `gflow auth status` (que prova a sessão) e **não** `credits`, porque em
    contas flow.google.com o saldo falha mesmo com a sessão boa.
    """
    args = ["auth", "status"]
    if profile:
        args += ["--profile", profile]
    try:
        proc = _rodar(args, timeout=180)
    except (FlowIndisponivel, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def modelo_do_roteiro(roteiro: Roteiro) -> str:
    alias = ALIASES.get(roteiro.modelo.strip().lower())
    if not alias:
        raise RoteiroInvalido(
            f"modelo {roteiro.modelo!r} não reconhecido. Use um de: "
            + ", ".join(sorted(set(ALIASES))))
    return alias


def validar(roteiro: Roteiro) -> None:
    """Recusa combinações que o Flow abortaria — antes de gastar crédito."""
    modelo = modelo_do_roteiro(roteiro)
    if roteiro.duracao == 10 and modelo != "omni-flash":
        raise RoteiroInvalido(
            "só o modelo omni-flash gera 10 s; nos Veo o máximo é 8 s (use "
            "`gflow video extend` para continuar um clipe).")
    if roteiro.resolucao not in {"360p", "720p"}:
        raise RoteiroInvalido("resolucao deve ser '360p' ou '720p'")
    if roteiro.resolucao != "720p" and modelo != "omni-flash":
        raise RoteiroInvalido(
            "só o omni-flash oferece escolha de resolução; nos Veo a saída é 720p.")
    if roteiro.aspect not in {"9:16", "16:9"}:
        raise RoteiroInvalido("aspect deve ser 9:16 (portrait) ou 16:9 (landscape)")


def custo_estimado(roteiro: Roteiro, quantidade: int = 1) -> int:
    """Créditos que a geração vai consumir (0 = não sei estimar)."""
    modelo = modelo_do_roteiro(roteiro)
    tabela = CUSTO.get(modelo)
    if isinstance(tabela, dict):
        por_duracao = tabela.get(roteiro.resolucao, {})
        return por_duracao.get(roteiro.duracao, 0) * quantidade
    return int(tabela or 0) * quantidade


@dataclass
class Geracao:
    comando: list[str]
    custo: int
    destino: Path


def montar_comando(roteiro: Roteiro, destino: Path, *, quantidade: int = 1,
                   profile: str | None = None, projeto: str | None = None,
                   extra: list[str] | None = None) -> Geracao:
    """Monta o comando do gflow (sem executá-lo)."""
    validar(roteiro)
    modelo = modelo_do_roteiro(roteiro)
    persona = (roteiro.personagem or "").strip()
    prompt = roteiro.prompt.strip()
    if not prompt:
        # Nunca enviar prompt vazio: o fallback de @personagem mandaria só a tag ao
        # Flow e a geração gastaria crédito sem descrever nada.
        raise RoteiroInvalido(
            "roteiro sem prompt — defina --prompt, ou --cena (o `gerar` monta o prompt "
            "pelo template base)")
    subcomando = "r2v" if roteiro.referencias else "t2v"

    args = ["video", subcomando, prompt, "--model", modelo,
            "--aspect", roteiro.aspect, "--duration", str(roteiro.duracao),
            "--count", str(quantidade), "--json", "-o", str(destino)]
    if modelo == "omni-flash":
        # Só o omni-flash expõe escolha de resolução; passar isso num modelo Veo
        # faz o gflow abortar antes do envio (exit 11).
        args += ["--resolution", roteiro.resolucao]
    if subcomando == "r2v":
        for referencia in roteiro.referencias:
            args += ["--ref", str(referencia)]
    if persona:
        if persona.startswith("@"):
            # o gflow resolve "@Nome" inline no próprio prompt — só não duplicar se o
            # template base já trouxer a tag (Character/Voice)
            if persona.lower() not in prompt.lower():
                args[2] = f"{prompt} {persona}".strip()
        else:
            args += ["--reference-entity", persona]
    if profile:
        args += ["--profile", profile]
    if projeto:
        args += ["--project", projeto]
    if extra:
        args += list(extra)
    return Geracao(comando=args, custo=custo_estimado(roteiro, quantidade),
                   destino=Path(destino))


def _variante_sem_referencias(roteiro: Roteiro) -> Roteiro | None:
    """Cópia do roteiro sem qualquer mecanismo de referência (``--ref`` e ``@tags``).

    Devolve None se já não houver nada a remover.
    """
    from dataclasses import replace

    from .prompt import sem_referencias

    tem_tags = "@" in roteiro.prompt
    if not roteiro.referencias and not tem_tags and not (roteiro.personagem or "").startswith("@"):
        return None
    # `personagem=None` é essencial: sem isso o montar_comando re-adiciona "@Nome" no fim
    # do prompt e a tentativa "sem referências" volta a falhar por menção.
    return replace(roteiro, referencias=[], personagem=None,
                   prompt=sem_referencias(roteiro.prompt))


def gerar(roteiro: Roteiro, destino: Path, *, quantidade: int = 1,
          profile: str | None = None, projeto: str | None = None,
          extra: list[str] | None = None) -> Path:
    """Gera o clipe no Flow e grava em `destino`.

    Contas servidas por ``flow.google.com`` **não carregam o catálogo de ativos**, então
    o gflow aborta com ``MentionIndexUnavailableError`` tanto para ``@menções`` no prompt
    quanto para ``--ref <arquivo>``. Nesse caso o envio é refeito sem nenhum dos dois —
    a Vó continua vindo do projeto e da descrição detalhada do prompt. O abort acontece
    antes do submit, então a tentativa falha não consome crédito.
    """
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    variantes: list[Roteiro] = [roteiro]
    alternativa = _variante_sem_referencias(roteiro)
    if alternativa is not None:
        variantes.append(alternativa)

    ultimo: tuple[int, str] = (0, "")
    for indice, variante in enumerate(variantes):
        geracao = montar_comando(variante, destino, quantidade=quantidade,
                                 profile=profile, projeto=projeto, extra=extra)
        proc = _rodar(geracao.comando)
        saida = f"{proc.stderr}\n{proc.stdout}"
        if destino.exists():
            return destino
        ultimo = (proc.returncode, saida)
        if "MentionIndexUnavailable" not in saida:
            break                        # erro de outra natureza: não insistir
        if indice + 1 < len(variantes):
            print("aviso: esta conta não carrega o catálogo de ativos do Flow — "
                  "gerando sem referências (a Vó vem do projeto e da descrição do "
                  "prompt; nada foi gasto na tentativa anterior)")
    codigo, saida = ultimo
    pistas = saida.strip().splitlines()[-6:]
    raise FlowIndisponivel(
        f"o gflow terminou com código {codigo} e não gerou {destino.name}. Códigos 11/28 "
        "significam aborto ANTES de enviar (sem gasto de crédito): combinação não "
        "suportada pela sua conta ou editor do Flow inacessível. Últimas linhas:\n"
        + "\n".join(pistas))
