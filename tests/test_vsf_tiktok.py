"""Contratos do upload no TikTok Studio.

Sem rede por padrão: o que depende do navegador só roda com ``VSF_TIKTOK_E2E=1``
(e, nesse caso, apenas verifica o portão de login — nunca publica nada).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.vsf_produce import producao, spec, tiktok_driver  # noqa: E402


def test_acao_invalida_e_recusada(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(ValueError):
        tiktok_driver.subir(video, acao="publico")


def test_video_inexistente_falha_antes_de_abrir_navegador(tmp_path):
    with pytest.raises(tiktok_driver.TikTokIndisponivel) as erro:
        tiktok_driver.subir(tmp_path / "nao-existe.mp4", acao="privado")
    assert "não encontrado" in str(erro.value)


def test_perfil_respeita_variavel_de_ambiente(tmp_path, monkeypatch):
    alvo = tmp_path / "perfil-teste"
    monkeypatch.setenv("VSF_TIKTOK_PROFILE", str(alvo))
    assert tiktok_driver.perfil() == alvo
    assert alvo.parent.exists()          # criado para o Playwright não reclamar


def test_textos_cobrem_portugues_e_ingles():
    for chave in ("publicar", "visibilidade", "privado", "legenda"):
        opcoes = tiktok_driver.TEXTOS[chave]
        assert "Only you" in opcoes or "Somente você" in opcoes or len(opcoes) >= 2
        assert any(o.isascii() for o in opcoes), f"{chave} sem alternativa em inglês"
    assert "Somente você" in tiktok_driver.TEXTOS["privado"]


def test_pasta_debug_e_criada(tmp_path, monkeypatch):
    monkeypatch.setenv("VSF_TIKTOK_DEBUG", str(tmp_path / "dbg"))
    assert tiktok_driver.pasta_debug().exists()


def test_legenda_do_tiktok_junta_titulo_e_marcacoes():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="CHEIRO DE DOMINGO",
                           prompt="p", titulo="Cheiro de domingo",
                           descricao="História de cozinha", tags=["#idosos"])
    legenda = producao.legenda_tiktok(roteiro)
    assert legenda.startswith("Cheiro de domingo")
    assert "#idosos" in legenda and "#shorts" in legenda and "#vosemfiltro" in legenda


def test_legenda_do_tiktok_nao_duplica_marcacao_existente():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="G", prompt="p", titulo="T",
                           tags=["#shorts", "#VoSemFiltro"])
    legenda = producao.legenda_tiktok(roteiro).lower()
    assert legenda.count("#shorts") == 1
    assert legenda.count("#vosemfiltro") == 1


def test_legenda_cai_para_o_gancho_quando_nao_ha_titulo():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="FILHO NÃO VEIO", prompt="p")
    assert producao.legenda_tiktok(roteiro).startswith("FILHO NÃO VEIO")


@pytest.mark.skipif(not os.environ.get("VSF_TIKTOK_E2E"),
                    reason="define VSF_TIKTOK_E2E=1 para falar com o TikTok")
def test_portao_de_login_do_studio(tmp_path):
    """Abre o Studio de verdade e exige ou a sessão, ou o erro claro de login.

    Sobe navegador (xvfb em ambiente sem tela) e **não publica nada**: o caminho
    sem sessão tem que terminar em LoginNecessario.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            navegador = tiktok_driver._abrir(pw, headless=True)
        except Exception as erro:                     # sem navegador instalado
            pytest.skip(f"navegador indisponível: {erro}")
        pagina = tiktok_driver._primeira_pagina(navegador)
        pagina.set_default_timeout(30000)
        pagina.goto(tiktok_driver.STUDIO_UPLOAD, wait_until="domcontentloaded")
        pagina.wait_for_timeout(4000)
        logado = tiktok_driver._logado(pagina)
        tem_campo = pagina.locator("input[type=file]").count() > 0
        navegador.close()
    # Ou está logado e a página de upload existe, ou não está e a URL é de login.
    assert logado is tem_campo, "login detectado sem campo de arquivo (ou o contrário)"
