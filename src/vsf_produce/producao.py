"""Orquestração do fluxo: gerar takes no Flow, editar o aprovado, publicar.

A pasta de cada roteiro guarda os takes brutos do Flow; o take aprovado vira um
projeto `.mlt` (mesmos presets do Shotcut) e sai renderizado em
``~/Videos/VoSemFiltro/Edited-Videos/Semana<N>/`` com o nome
``vosemfiltro-semana<N>-<slug>.mp4``.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import flow_driver, spec
from .editor_mlt import montar_projeto, render, texto_aplicado
from .spec import Roteiro, Take, pasta_do_roteiro


class ProducaoInvalida(RuntimeError):
    """O roteiro não está pronto para a etapa pedida."""


# --------------------------------------------------------------- transcrição

def _cues_do_transcript(transcript, *, max_palavras: int = 2,
                        max_caracteres: int = 16) -> list[tuple[float, float, str]]:
    """Agrupa palavras em blocos curtos, no estilo de legenda do canal."""
    cues: list[tuple[float, float, str]] = []
    linha, inicio, palavras = [], None, 0
    for segmento in transcript.segments:
        palavras_do_segmento = list(segmento.words or [])
        if not palavras_do_segmento:
            texto = segmento.text.strip()
            if texto:
                cues.append((segmento.start, segmento.end, texto))
            continue
        for palavra in palavras_do_segmento:
            texto = palavra.text.strip()
            if not texto:
                continue
            if inicio is None:
                inicio = palavra.start
            candidato = " ".join([*linha, texto])
            if linha and (palavras >= max_palavras or len(candidato) > max_caracteres):
                cues.append((inicio, palavra.start, "\n".join(linha)))
                linha, palavras, inicio = [texto], 1, palavra.start
            else:
                linha.append(texto)
                palavras += 1
            if len(linha) == 2:
                cues.append((inicio, palavra.start, "\n".join(linha)))
                linha, palavras, inicio = [], 0, None
    if linha and inicio is not None:
        cues.append((inicio, inicio + 1.0, "\n".join(linha)))
    return cues


def _ts_srt(segundos: float) -> str:
    milissegundos = max(int(round(segundos * 1000)), 0)
    horas, resto = divmod(milissegundos, 3_600_000)
    minutos, resto = divmod(resto, 60_000)
    seg, ms = divmod(resto, 1000)
    return f"{horas:02d}:{minutos:02d}:{seg:02d},{ms:03d}"


def escrever_srt(transcript, destino: Path) -> Path:
    blocos = []
    for indice, (inicio, fim, texto) in enumerate(_cues_do_transcript(transcript), start=1):
        blocos.append(f"{indice}\n{_ts_srt(inicio)} --> {_ts_srt(max(fim, inicio + 0.2))}\n"
                      f"{texto}")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n\n".join(blocos) + "\n", encoding="utf-8")
    return destino


def transcrever(clip: Path, destino: Path | None = None) -> Path:
    """Gera o SRT do clipe com o Whisper já configurado no projeto."""
    from src.config import load_config
    from src.transcriber import transcribe_audio

    clip = Path(clip)
    destino = Path(destino or clip.with_suffix(".srt"))
    transcript = transcribe_audio(load_config(), clip, language="pt")
    return escrever_srt(transcript, destino)


# ------------------------------------------------------------------ geração

def montar_prompt_do_roteiro(roteiro: Roteiro) -> str:
    """Prompt de geração a partir do template base.

    Ordem de precedência: **ação** (BLOCO A + a fala/gesto do roteiro — a forma que o
    canal usa), depois um prompt literal já gravado, e por último o bloco com as
    ``@tags`` de referência (que o driver remove sozinho se a conta não as resolver).
    """
    from . import prompt as prompt_mod

    if roteiro.acao.strip():
        return prompt_mod.prompt_com_acao(roteiro.acao, roteiro.prompt_template)
    if roteiro.prompt.strip():
        return roteiro.prompt
    personagem = (roteiro.personagem or "").strip()
    nome = personagem.lstrip("@").strip() if personagem.startswith("@") else \
        prompt_mod.PERSONAGEM_PADRAO
    return prompt_mod.prompt_com_referencias(personagem=nome,
                                             referencia_quadro=roteiro.referencia_quadro,
                                             template=roteiro.prompt_template)


def referencias_do_roteiro(roteiro: Roteiro) -> list[str]:
    """Referências do roteiro; sem nenhuma definida, usa as folhas padrão do personagem."""
    from . import referencias as assets

    if roteiro.referencias:
        return list(roteiro.referencias)
    if not roteiro.usar_referencias_padrao:
        return []
    return [str(arquivo) for arquivo in assets.referencias_padrao()]


def gerar_takes(roteiro: Roteiro, *, base: Path | None = None, quantidade: int = 1,
                profile: str | None = None, projeto: str | None = None,
                dry_run: bool = False) -> list[Take]:
    """Gera `quantidade` takes novos no Flow e registra cada um no roteiro."""
    roteiro.prompt = montar_prompt_do_roteiro(roteiro)
    roteiro.referencias = referencias_do_roteiro(roteiro)
    if roteiro.referencias:
        nomes = ", ".join(Path(r).name for r in roteiro.referencias)
        print(f"referências ({len(roteiro.referencias)}): {nomes}")
    flow_driver.validar(roteiro)
    pasta = pasta_do_roteiro(roteiro, base) / "takes"
    pasta.mkdir(parents=True, exist_ok=True)
    custo = flow_driver.custo_estimado(roteiro, quantidade)
    novos: list[Take] = []
    for _ in range(quantidade):
        numero = len(roteiro.takes) + 1
        destino = pasta / f"take{numero}.mp4"
        geracao = flow_driver.montar_comando(roteiro, destino, profile=profile,
                                             projeto=projeto)
        if dry_run:
            print(" ".join(geracao.comando), f"\n  (custo estimado: {geracao.custo} "
                  "créditos — nada foi gasto)")
            continue
        flow_driver.gerar(roteiro, destino, profile=profile, projeto=projeto)
        take = Take(arquivo=f"takes/take{numero}.mp4", modelo=roteiro.modelo)
        roteiro.takes.append(take)
        novos.append(take)
        print(f"take{numero} pronto ({geracao.destino.stat().st_size // 1024} KB)")
    if not dry_run:
        spec.salvar(roteiro, base)
    print(f"créditos estimados nesta rodada: {custo}")
    return novos


# -------------------------------------------------------------------- edição

def editar(roteiro: Roteiro, *, base: Path | None = None,
           srt: Path | None = None, transcrever_se_faltar: bool = True) -> Path:
    """Edita o take aprovado: corta o silêncio inicial, aplica gancho e legendas."""
    take = roteiro.aprovado_take()
    if take is None:
        raise ProducaoInvalida(
            "nenhum take aprovado neste roteiro — aprove um take antes de editar "
            "(a página local ou `vsf-produce aprovar <slug> <take>`).")
    pasta = pasta_do_roteiro(roteiro, base)
    clipe = pasta / take.arquivo
    if not clipe.exists():
        raise ProducaoInvalida(f"o take aprovado não está no disco: {clipe}")

    arquivo_srt = srt or spec.srt_do_roteiro(roteiro, base)
    if arquivo_srt is None and transcrever_se_faltar:
        arquivo_srt = transcrever(clipe, pasta / "legenda.srt")
        roteiro.legenda_srt = "legenda.srt"
        spec.salvar(roteiro, base)

    presets = roteiro.presets_efetivos()
    projeto = montar_projeto(clipe, pasta / "projeto.mlt", gancho=roteiro.gancho,
                             srt=arquivo_srt.read_text(encoding="utf-8") if arquivo_srt else "",
                             presets=presets, titulo=roteiro.titulo or roteiro.slug)
    destino = roteiro.pasta_saida() / roteiro.nome_saida()
    return render(projeto, destino, presets=presets)


def verificar_motor(presets=None) -> bool:
    """True se este ambiente consegue renderizar texto (filtros Qt do Shotcut)."""
    return texto_aplicado(presets)


# ---------------------------------------------------------------- publicação

def publicar(roteiro: Roteiro, video: Path | None = None, *, base: Path | None = None,
             privacidade: str = "private", publicar_em: str | None = None,
             categoria: str = "22") -> dict:
    """Sobe o vídeo editado para o canal e devolve o recurso criado pela API."""
    from .publisher import MetadadosVideo, enviar

    if video is None:
        video = roteiro.pasta_saida() / roteiro.nome_saida()
    video = Path(video)
    if not video.exists():
        raise ProducaoInvalida(f"vídeo não encontrado para publicar: {video}")
    descricao = roteiro.descricao or ""
    if "#shorts" not in descricao.lower():
        descricao = (descricao + "\n\n#shorts").strip()
    meta = MetadadosVideo(titulo=roteiro.titulo or roteiro.slug, descricao=descricao,
                          tags=roteiro.tags, categoria=categoria,
                          privacidade=privacidade, publicar_em=publicar_em)
    return enviar(video, meta)


# --------------------------------------------------------------------- status

def resumo(base: Path | None = None) -> list[dict]:
    """Visão para a página local e para o terminal."""
    linhas = []
    for roteiro in spec.listar(base):
        pasta = pasta_do_roteiro(roteiro, base)
        saida = roteiro.pasta_saida() / roteiro.nome_saida()
        srt = spec.srt_do_roteiro(roteiro, base)
        linhas.append({
            "slug": roteiro.slug, "semana": roteiro.semana, "gancho": roteiro.gancho,
            "titulo": roteiro.titulo, "preset": roteiro.preset,
            "modelo": roteiro.modelo, "duracao": roteiro.duracao,
            "takes": [{"arquivo": t.arquivo, "status": t.status,
                       "existe": (pasta / t.arquivo).exists(),
                       "motivos": t.motivos} for t in roteiro.takes],
            "aprovado": roteiro.aprovado,
            "legenda": str(srt) if srt else None,
            "editado": str(saida) if saida.exists() else None,
            "custo_por_take": flow_driver.custo_estimado(roteiro),
        })
    return linhas


def slugificar(texto: str) -> str:
    """Slug seguro para nome de arquivo a partir do gancho/título."""
    base = re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")
    return base[:48] or "short"


def legenda_tiktok(roteiro: Roteiro) -> str:
    """Legenda do post no TikTok: título + tags + a marca do canal."""
    partes = [roteiro.titulo or roteiro.gancho]
    if roteiro.descricao:
        partes.append(roteiro.descricao)
    marcacoes = list(roteiro.tags)
    for marca in ("#shorts", "#vosemfiltro"):
        if marca not in [m.lower() for m in marcacoes]:
            marcacoes.append(marca)
    partes.append(" ".join(marcacoes))
    return "\n\n".join(p for p in partes if p).strip()
