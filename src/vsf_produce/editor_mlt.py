"""Gerador de projeto Shotcut (.mlt) + render headless com `melt`.

Por que gerar `.mlt` em vez de recodificar com ffmpeg: os presets de gancho e de
legenda do usuário vivem no Shotcut, e o `melt` (motor que o Shotcut embute) aplica
esses valores exatamente como o export manual — mesmo resultado, sem tradução de
estilo. O projeto gerado pode ser aberto no Shotcut para revisão a qualquer momento.

Limite conhecido: os filtros de texto do Shotcut são Qt e **exigem um servidor de
vídeo**. Rodando de uma sessão gráfica funciona; em cron/headless é preciso
``xvfb-run`` (o erro é detectado e a mensagem é explícita).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import xml.sax.saxutils as sax
from dataclasses import dataclass
from pathlib import Path

from .presets import FPS, HEIGHT, WIDTH, Presets
from .silence import duration as clip_duration
from .silence import leading_silence

FLATPAK_APP = "org.shotcut.Shotcut"


class ErroDeRender(RuntimeError):
    """Falha ao renderizar o projeto .mlt."""


class SemServidorGrafico(ErroDeRender):
    """O melt não conseguiu carregar os filtros de texto (Qt) sem display."""


def comando_melt() -> list[str]:
    """Comando para invocar o melt: variável de ambiente, flatpak do Shotcut ou PATH."""
    explicito = os.environ.get("VSF_MELT")
    if explicito:
        return explicito.split()
    if shutil.which("flatpak"):
        apps = subprocess.run(["flatpak", "list", "--app", "--columns=application"],
                              capture_output=True, text=True).stdout
        if FLATPAK_APP in apps:
            return ["flatpak", "run", "--command=melt", FLATPAK_APP]
    if shutil.which("melt"):
        return ["melt"]
    raise ErroDeRender(
        "melt não encontrado. Instale o Shotcut (flatpak) ou defina VSF_MELT.")


def ts(segundos: float) -> str:
    """Tempo no formato HH:MM:SS.mmm usado pelo MLT."""
    segundos = max(segundos, 0.0)
    horas, resto = divmod(segundos, 3600)
    minutos, seg = divmod(resto, 60)
    return f"{int(horas):02d}:{int(minutos):02d}:{seg:06.3f}"


def quebrar_gancho(texto: str, max_caracteres: int = 16) -> str:
    """Quebra o gancho em até duas linhas balanceadas.

    O filtro Text:Simple encaixa o texto na caixa do preset; uma linha longa demais
    é esticada até as bordas e sai cortada (comprovado no render de teste com
    "CHEIRO DE DOMINGO" numa linha). Duas linhas curtas mantêm a fonte no tamanho
    do preset.
    """
    texto = texto.strip()
    if "\n" in texto or len(texto) <= max_caracteres:
        return texto
    palavras = texto.split()
    metade = len(texto) / 2
    linha1, atual = [], 0.0
    for palavra in palavras:
        if atual + len(palavra) / 2 > metade and linha1:
            break
        linha1.append(palavra)
        atual += len(palavra) + 1
    return " ".join(linha1) + "\n" + " ".join(palavras[len(linha1):])


def _ler_ts_srt(texto: str) -> float:
    """``00:00:03,000`` -> 3.0"""
    achado = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)", texto)
    if not achado:
        raise ErroDeRender(f"tempo de SRT inválido: {texto!r}")
    h, m, s, ms = (int(g) for g in achado.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def _escrever_ts_srt(segundos: float) -> str:
    total = max(int(round(segundos * 1000)), 0)
    h, resto = divmod(total, 3_600_000)
    m, resto = divmod(resto, 60_000)
    s, ms = divmod(resto, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def deslocar_srt(srt: str, offset: float) -> str:
    """Desloca os tempos do SRT em ``-offset`` segundos.

    O filtro de legendas do MLT casa os tempos do SRT com o **tempo da timeline**, não
    com o do clipe de origem (verificado com um teste controlado de 6 s com corte de
    2,9 s). Como o SRT é transcrito do clipe inteiro e a timeline começa no ponto do
    corte, sem este deslocamento toda legenda sai adiantada exatamente pelo tempo
    cortado. Cues que caem inteiros dentro do corte são descartados.
    """
    if not srt.strip():
        return srt
    blocos: list[str] = []
    for bloco in re.split(r"\n\s*\n", srt.strip()):
        linhas = bloco.strip().splitlines()
        indice = next((i for i, linha in enumerate(linhas) if "-->" in linha), None)
        if indice is None:
            continue
        inicio, fim = (_ler_ts_srt(parte) for parte in linhas[indice].split("-->")[:2])
        inicio, fim = max(inicio - offset, 0.0), fim - offset
        if fim <= 0:
            continue
        texto = "\n".join(linhas[indice + 1:])
        blocos.append(f"{len(blocos) + 1}\n{_escrever_ts_srt(inicio)} --> "
                      f"{_escrever_ts_srt(fim)}\n{texto}")
    return "\n\n".join(blocos) + "\n" if blocos else ""


def _propriedades(valores: dict[str, object]) -> str:
    linhas = []
    for chave, valor in valores.items():
        if valor is None:
            continue
        linhas.append(f'      <property name="{chave}">{sax.escape(str(valor))}</property>')
    return "\n".join(linhas)


@dataclass
class Projeto:
    mlt: Path
    entrada: Path
    inicio: float
    fim: float
    gancho: str
    fim_gancho: float


def montar_projeto(clip: Path, destino_mlt: Path, *, gancho: str,
                   srt: str = "", presets: Presets | None = None,
                   trim_silencio: bool = True, titulo: str = "") -> Projeto:
    """Escreve o `.mlt` com o clipe, o gancho e as legendas já posicionados."""
    presets = presets or Presets()
    # Caminho ABSOLUTO: o melt roda dentro do flatpak do Shotcut, que não resolve
    # caminho relativo ao repositório — sem isso o produtor não abre e o render sai
    # em branco (só áudio), como aconteceu na primeira execução real.
    clip = Path(clip).expanduser().resolve()
    if not clip.exists():
        raise ErroDeRender(f"clipe não encontrado: {clip}")
    total = clip_duration(clip)
    if total <= 0:
        raise ErroDeRender(f"não consegui ler a duração de {clip}")
    inicio = leading_silence(clip, noise_db=presets.silence_db,
                             min_seconds=presets.silence_min,
                             keep=presets.silence_keep) if trim_silencio else 0.0
    fim = total
    fim_gancho = min(inicio + presets.hook_dur, fim)
    gancho_txt = quebrar_gancho(gancho)
    if srt and inicio:
        # a timeline começa no ponto do corte; o SRT é do clipe inteiro
        srt = deslocar_srt(srt, inicio)

    g = presets.gancho
    filtro_gancho = {
        "argument": gancho_txt, "geometry": g.geometry, "family": g.font,
        "size": g.size, "weight": g.weight, "style": "normal",
        "fgcolour": g.fgcolour, "bgcolour": "#00000000", "olcolour": g.outline_colour,
        "pad": 0, "halign": g.halign, "valign": g.valign, "outline": g.outline,
        "underline": 0, "strikethrough": 0, "opacity": 1,
        "mlt_service": "dynamictext", "shotcut:filter": "dynamicText",
        "shotcut:usePointSize": 1, "shotcut:pointSize": g.point_size,
        "shotcut:animIn": g.anim_in, "shotcut:animOut": ts(fim_gancho - inicio),
    }
    leg = presets.legenda
    filtro_legenda = {
        "text": srt, "geometry": leg.geometry, "family": leg.font, "size": leg.size,
        "weight": leg.weight, "style": "normal", "fgcolour": leg.fgcolour,
        "bgcolour": "#00000000", "olcolour": leg.outline_colour, "pad": 0,
        "halign": leg.halign, "valign": leg.valign, "outline": leg.outline,
        "mlt_service": "subtitle", "shotcut:filter": "subtitles",
        "shotcut:usePointSize": 1, "shotcut:pointSize": leg.point_size,
    }

    xml = f"""<?xml version="1.0" standalone="no"?>
<mlt LC_NUMERIC="C" version="7.41.0" title="{sax.escape(titulo or 'Vó Sem Filtro')}" producer="main_bin">
  <profile description="vertical 1080x1920" width="{WIDTH}" height="{HEIGHT}" progressive="1" sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="9" display_aspect_den="16" frame_rate_num="{FPS}" frame_rate_den="1" colorspace="709"/>
  <producer id="black" in="00:00:00.000" out="00:00:00.000">
    <property name="mlt_service">color</property>
    <property name="resource">0</property>
    <property name="length">1</property>
    <property name="eof">pause</property>
  </producer>
  <playlist id="main_bin">
    <property name="xml_retain">1</property>
  </playlist>
  <producer id="producer0" in="{ts(inicio)}" out="{ts(fim)}">
    <property name="length">{ts(total)}</property>
    <property name="eof">pause</property>
    <property name="resource">{sax.escape(str(clip))}</property>
    <property name="mlt_service">avformat-novalidate</property>
    <property name="seekable">1</property>
    <property name="format">3</property>
    <property name="vstream">0</property>
    <property name="astream">1</property>
  </producer>
  <playlist id="playlist0">
    <entry producer="producer0" in="{ts(inicio)}" out="{ts(fim)}"/>
  </playlist>
  <tractor id="tractor0" in="{ts(inicio)}" out="{ts(fim)}">
    <property name="shotcut">1</property>
    <track producer="playlist0"/>
    <filter id="gancho" in="{ts(inicio)}" out="{ts(fim_gancho)}">
{_propriedades(filtro_gancho)}
    </filter>
    <filter id="legenda_entrada">
      <property name="text">{sax.escape(srt)}</property>
      <property name="mlt_service">subtitle_feed</property>
      <property name="shotcut:hidden">1</property>
    </filter>
    <filter id="legenda">
{_propriedades(filtro_legenda)}
    </filter>
  </tractor>
</mlt>
"""
    destino_mlt = Path(destino_mlt)
    destino_mlt.parent.mkdir(parents=True, exist_ok=True)
    destino_mlt.write_text(xml, encoding="utf-8")
    return Projeto(mlt=destino_mlt, entrada=clip, inicio=inicio, fim=fim,
                   gancho=gancho_txt, fim_gancho=fim_gancho)


_MLT_PROBE = """<?xml version="1.0" standalone="no"?>
<mlt LC_NUMERIC="C" version="7.41.0" title="probe" producer="main_bin">
  <profile description="probe" width="{w}" height="{h}" progressive="1" sample_aspect_num="1" sample_aspect_den="1" display_aspect_num="9" display_aspect_den="16" frame_rate_num="{fps}" frame_rate_den="1" colorspace="709"/>
  <producer id="bg" in="00:00:00.000" out="00:00:00.050">
    <property name="mlt_service">color</property>
    <property name="resource">#404040</property>
    <property name="length">4</property>
  </producer>
  <playlist id="playlist0">
    <entry producer="bg" in="00:00:00.000" out="00:00:00.050"/>
  </playlist>
  <tractor id="tractor0" in="00:00:00.000" out="00:00:00.050">
    <property name="shotcut">1</property>
    <track producer="playlist0"/>
    <filter id="gancho">
{props}
    </filter>
  </tractor>
</mlt>
"""


def texto_aplicado(presets: Presets | None = None, *, melter: list[str] | None = None,
                   pasta: Path | None = None) -> bool:
    """Testa se o melt realmente desenha texto (filtros Qt) neste processo.

    O aviso "requires a X11 or Wayland environment" aparece no stderr mesmo quando o
    texto sai corretamente, então ele não serve de sinal. Em vez disso, renderiza um
    quadro com fundo cinza chapado e verifica se há glifos brancos — determinístico.
    """
    presets = presets or Presets()
    g = presets.gancho
    props = {
        "argument": "TESTE", "geometry": g.geometry, "family": g.font, "size": g.size,
        "weight": g.weight, "style": "normal", "fgcolour": g.fgcolour,
        "bgcolour": "#00000000", "olcolour": g.outline_colour, "pad": 0,
        "halign": g.halign, "valign": g.valign, "outline": g.outline,
        "mlt_service": "dynamictext", "shotcut:filter": "dynamicText",
        "shotcut:usePointSize": 1, "shotcut:pointSize": g.point_size,
    }
    pasta = Path(pasta or os.environ.get("TMPDIR", "/tmp"))
    mlt = pasta / "_vsf_probe.mlt"
    png = pasta / "_vsf_probe.png"
    mlt.write_text(_MLT_PROBE.format(w=WIDTH, h=HEIGHT, fps=FPS, props=_propriedades(props)),
                   encoding="utf-8")
    if png.exists():
        png.unlink()
    subprocess.run([*(melter or comando_melt()), str(mlt), "-consumer",
                    f"avformat:{png}", "frames=1"], capture_output=True, text=True)
    if not png.exists():
        return False
    from PIL import Image  # dependência já usada no projeto

    with Image.open(png) as imagem:
        faixa = imagem.convert("L").crop((0, HEIGHT // 3, WIDTH, HEIGHT))
        pixels = faixa.tobytes()          # tobytes(): estável entre versões do Pillow
    return sum(1 for valor in pixels if valor > 200) > 200


def render(projeto: Projeto | Path, saida: Path, *, codec: str = "h264_nvenc",
           preset: str = "p6", video_bitrate: str = "10M",
           audio_bitrate: str = "256k", melter: list[str] | None = None,
           verificar_texto: bool = True, presets: Presets | None = None) -> Path:
    """Renderiza o `.mlt` para MP4 (1080x1920@60, NVENC p6)."""
    mlt = projeto.mlt if isinstance(projeto, Projeto) else Path(projeto)
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    melter = melter or comando_melt()
    if verificar_texto and not texto_aplicado(presets, melter=melter):
        raise SemServidorGrafico(
            "o melt não conseguiu desenhar texto (filtros Qt do Shotcut) neste "
            "processo: o vídeo sairia SEM gancho e SEM legendas. Rode de uma sessão "
            "gráfica ou instale o xvfb (`sudo apt install xvfb`) e rode via "
            "`xvfb-run -a <comando>`.")
    cmd = [*melter, str(mlt), "-consumer", f"avformat:{saida}",
           f"vcodec={codec}", f"preset={preset}", "rc=vbr", f"vb={video_bitrate}",
           "acodec=aac", f"ab={audio_bitrate}", "f=mp4"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not saida.exists():
        cauda = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise ErroDeRender(f"melt falhou (código {proc.returncode}):\n{cauda}")
    return saida
