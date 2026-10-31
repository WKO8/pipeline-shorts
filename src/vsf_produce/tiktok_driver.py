"""Upload no TikTok pelo TikTok Studio (web), com visibilidade privada.

Não usa a Content Posting API de propósito: o **Direct Post** exige auditoria do app no
TikTok for Developers e, para cliente não auditado, é bloqueado com
``unaudited_client_can_only_post_to_private_accounts`` quando a conta é pública — que é o
caso do @vosemfiltro.ia. O caminho da API que funcionaria sem auditoria é o
``video.upload`` (rascunhos/inbox), mas ainda exige app aprovado.

Aqui dirigimos o próprio TikTok Studio num Chromium com perfil persistente — a mesma
estratégia que o `gflow-cli` usa com o Flow. A visibilidade é fixada em **"Somente
você"** (privado); o vídeo nunca vai ao público sem uma ação sua.

Login: uma vez, com ``tiktok-login`` (o perfil fica em ``~/.local/share/vsf-produce/``).
Como o Playwright não lê o perfil criptografado do Brave, o login é feito dentro do
perfil dedicado — igual ao gflow.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

STUDIO_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload"
LOGIN_URL = "https://www.tiktok.com/login"
PERFIL_PADRAO = Path.home() / ".local" / "share" / "vsf-produce" / "tiktok-profile"

# Textos que o Studio usa para cada controle (pt-BR primeiro, inglês como reserva).
TEXTOS = {
    "publicar": ["Publicar", "Post"],
    "visibilidade": ["Quem pode ver este vídeo", "Who can watch this video",
                     "Quem pode assistir", "Who can watch"],
    "privado": ["Somente você", "Only you"],
    "rascunho": ["Salvar como rascunho", "Salvar rascunho", "Save as draft", "Draft"],
    "processando": ["Processando", "Processing", "Carregando", "Uploading",
                    "Fazendo upload", "Enviando"],
    "legenda": ["Descreva o seu vídeo", "Describe your video", "Adicione uma legenda",
                "Add a caption", "Legenda"],
}


class TikTokIndisponivel(RuntimeError):
    """Falha de automação no TikTok Studio (não necessariamente culpa do vídeo)."""


class LoginNecessario(TikTokIndisponivel):
    """O perfil dedicado não tem sessão válida do TikTok."""


@dataclass
class ResultadoTikTok:
    acao: str
    url: str
    privado: bool
    screenshot: str | None = None


def perfil() -> Path:
    caminho = Path(os.environ.get("VSF_TIKTOK_PROFILE", PERFIL_PADRAO))
    caminho.parent.mkdir(parents=True, exist_ok=True)
    return caminho


def pasta_debug() -> Path:
    base = Path(os.environ.get("VSF_TIKTOK_DEBUG", "data/vsf_produce/tiktok_debug"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _abrir(pw, *, headless: bool = False):
    """Chromium com perfil persistente; usa o Chrome do sistema quando existir."""
    canal = os.environ.get("VSF_TIKTOK_BROWSER", "chrome").strip() or None
    opcoes = dict(user_data_dir=str(perfil()), headless=headless, locale="pt-BR",
                  args=["--disable-blink-features=AutomationControlled"],
                  ignore_default_args=["--enable-automation"], viewport=None)
    if canal:
        try:
            return pw.chromium.launch_persistent_context(channel=canal, **opcoes)
        except Exception:
            pass                                  # sem Chrome do sistema: usa o bundlado
    return pw.chromium.launch_persistent_context(**opcoes)


def _primeira_pagina(navegador):
    return navegador.pages[0] if navegador.pages else navegador.new_page()


def _logado(pagina) -> bool:
    url = pagina.url
    return "/login" not in url and "passport" not in url


def _clicar_por_texto(pagina, chaves: list[str], *, timeout: int = 8000) -> str | None:
    """Clica no primeiro texto que existir; devolve qual funcionou."""
    for chave in chaves:
        try:
            alvo = pagina.get_by_text(chave, exact=True).first
            if alvo.count() and alvo.is_visible():
                alvo.click(timeout=timeout)
                return chave
        except Exception:
            continue
    return None


def _diagnostico(pagina, rotulo: str) -> str:
    """Salva screenshot + HTML quando algo não é encontrado (auto-diagnóstico)."""
    carimbo = time.strftime("%Y%m%d-%H%M%S")
    png = pasta_debug() / f"{rotulo}-{carimbo}.png"
    html = pasta_debug() / f"{rotulo}-{carimbo}.html"
    try:
        pagina.screenshot(path=str(png), full_page=False)
        html.write_text(pagina.content(), encoding="utf-8")
    except Exception:
        pass
    return str(png)


def login(timeout_segundos: int = 420) -> str:
    """Abre o navegador para o login manual no perfil dedicado e espera a sessão."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        navegador = _abrir(pw)
        pagina = _primeira_pagina(navegador)
        pagina.set_default_timeout(30000)
        pagina.goto(LOGIN_URL, wait_until="domcontentloaded")
        print("Faça o login (QR no celular ou senha) nesta janela. Não a feche.\n"
              "   Alternativa sem tela: rode com xvfb-run -a e faça o login pelo QR "
              "não é possível — use um monitor/desktop remoto.")
        limite = time.time() + timeout_segundos
        while time.time() < limite:
            if _logado(pagina):
                pagina.goto(STUDIO_UPLOAD, wait_until="domcontentloaded")
                pagina.wait_for_timeout(4000)
                if "tiktokstudio" in pagina.url:
                    print("sessão do TikTok salva em", perfil())
                    navegador.close()
                    return pagina.url
            pagina.wait_for_timeout(3000)
        png = _diagnostico(pagina, "login-timeout")
        navegador.close()
    raise LoginNecessario(f"login não concluído em {timeout_segundos}s "
                          f"(captura: {png})")


def subir(video: Path, *, legenda: str = "", acao: str = "privado",
          headless: bool = False, espera_maxima: int = 900) -> ResultadoTikTok:
    """Sobe o vídeo no Studio.

    ``acao="privado"`` deixa tudo pronto e clica em Publicar com visibilidade
    "Somente você"; ``acao="rascunho"`` preenche e **não** publica, deixando o rascunho
    para revisão na página.
    """
    from playwright.sync_api import sync_playwright

    if acao not in {"privado", "rascunho"}:
        raise ValueError("acao deve ser 'privado' ou 'rascunho'")
    video = Path(video)
    if not video.exists():
        raise TikTokIndisponivel(f"vídeo não encontrado: {video}")

    with sync_playwright() as pw:
        navegador = _abrir(pw, headless=headless)
        pagina = _primeira_pagina(navegador)
        pagina.set_default_timeout(45000)
        pagina.goto(STUDIO_UPLOAD, wait_until="domcontentloaded")
        pagina.wait_for_timeout(4000)
        if not _logado(pagina):
            navegador.close()
            raise LoginNecessario(
                "sem sessão do TikTok no perfil dedicado. Rode `tiktok-login` uma vez "
                f"(perfil: {perfil()}).")

        campo = pagina.locator("input[type=file]").first
        try:
            campo.set_input_files(str(video), timeout=60000)
        except Exception as erro:
            png = _diagnostico(pagina, "sem-input-arquivo")
            navegador.close()
            raise TikTokIndisponivel(
                f"não encontrei o campo de arquivo do Studio ({erro}). Captura: {png}")

        # espera o processamento: o botão Publicar só habilita quando o vídeo está pronto
        inicio = time.time()
        while time.time() - inicio < espera_maxima:
            pagina.wait_for_timeout(5000)
            publicar = pagina.get_by_text(TEXTOS["publicar"][0], exact=True).first
            if publicar.count():
                try:
                    if publicar.is_enabled():
                        break
                except Exception:
                    break
        else:
            png = _diagnostico(pagina, "processamento-lento")
            navegador.close()
            raise TikTokIndisponivel(
                f"o TikTok não terminou de processar em {espera_maxima}s (captura: {png})")

        if legenda:
            for candidato in TEXTOS["legenda"]:
                try:
                    editor = pagina.locator("[contenteditable=true]").first
                    if editor.count():
                        editor.click()
                        editor.type(legenda[:2200], delay=8)
                        break
                except Exception:
                    _ = candidato
                    continue

        privado = False
        for visibilidade in TEXTOS["visibilidade"]:
            try:
                alvo = pagina.get_by_text(visibilidade, exact=False).first
                if alvo.count() and alvo.is_visible():
                    alvo.click()
                    if _clicar_por_texto(pagina, TEXTOS["privado"]):
                        privado = True
                    break
            except Exception:
                continue

        png = str(pasta_debug() / f"pre-{acao}-{time.strftime('%Y%m%d-%H%M%S')}.png")
        try:
            pagina.screenshot(path=png, full_page=False)
        except Exception:
            png = None

        if acao == "rascunho":
            navegador.close()
            return ResultadoTikTok(acao=acao, url=pagina.url, privado=privado,
                                   screenshot=png)

        clicou = _clicar_por_texto(pagina, TEXTOS["publicar"], timeout=15000)
        if not clicou:
            png = _diagnostico(pagina, "sem-botao-publicar")
            navegador.close()
            raise TikTokIndisponivel(
                f"não encontrei o botão de publicar (captura: {png})")
        pagina.wait_for_timeout(6000)
        url = pagina.url
        navegador.close()
        return ResultadoTikTok(acao=acao, url=url, privado=privado, screenshot=png)
