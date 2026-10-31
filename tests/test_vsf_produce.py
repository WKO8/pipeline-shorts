"""Contratos da produção automática: presets, corte de silêncio, aprovação e .mlt.

Os testes não tocam a rede nem gastam crédito: a geração no Flow é verificada pelo
comando montado, e a edição pelo XML gerado (mais um render real, marcado como
`render`, que só roda quando existe o `melt`).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.vsf_produce import editor_mlt, flow_driver, producao, spec  # noqa: E402
from src.vsf_produce import prompt as prompt_mod  # noqa: E402
from src.vsf_produce.presets import Presets  # noqa: E402

CLIPE_REAL = Path.home() / "Videos" / "VoSemFiltro" / "Semana 2" / "v1.mp4"


def _clipe_falso(destino: Path, segundos: float = 3.0) -> Path:
    """Clipe com 1 s de silêncio no começo e depois tom — sem depender de arquivo real."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"anullsrc=r=48000:cl=stereo:d=1.0", "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=48000:duration={segundos - 1.0:.1f}",
        "-f", "lavfi", "-i", f"color=c=blue:s=720x1280:d={segundos}", "-filter_complex",
        "[0:a][1:a]concat=n=2:v=0:a=1[a]", "-map", "2:v", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(destino),
    ], check=True, capture_output=True)
    return destino


# --------------------------------------------------------------- presets

def test_presets_reproduzem_os_valores_do_shotcut():
    p = Presets()
    assert (p.gancho.font, p.gancho.size, p.gancho.outline) == ("Anton", 173, 35)
    assert (p.legenda.font, p.legenda.size, p.legenda.outline) == ("Montserrat Black", 105, 20)
    # o gancho saiu de "-11 897 1080 621 1" (terminava em 1518 e cobria a legenda)
    assert p.gancho.geometry == "-11 559 1080 621 1"
    assert p.legenda.geometry == "5 841 1071 599 1"
    assert p.hook_dur == 2.4


def test_ajuste_fino_sobrescreve_apenas_o_informado():
    p = Presets.from_dict({"gancho": {"outline": 12}, "hook_dur": 3.1})
    assert p.gancho.outline == 12
    assert p.gancho.font == "Anton"          # preservado
    assert p.legenda.size == 105             # preservado
    assert p.hook_dur == 3.1


def test_roteiro_usa_preset_nomeado_e_ajustes():
    roteiro = spec.Roteiro(slug="x", semana=1, gancho="G", prompt="P",
                           preset="anton", gancho_segundos=3.0,
                           presets={"gancho": {"outline": 5}})
    efetivos = roteiro.presets_efetivos()
    assert efetivos.hook_dur == 3.0
    assert efetivos.gancho.outline == 5
    assert efetivos.legenda.font == "Montserrat Black"


# ------------------------------------------------------- gancho / silêncio

def test_prompt_com_acao_monta_bloco_a_mais_a_acao():
    """A forma operacional do canal: BLOCO A + a fala/gesto em inglês."""
    texto = prompt_mod.prompt_com_acao('She narrows her eyes and says: "Filho que só '
                                       'liga quando precisa?"')
    assert texto.startswith("Vertical 9:16 smartphone-style video.")
    assert "A 78-year-old Brazilian grandmother" in texto
    assert "No subtitles, no on-screen text, no watermark." in texto
    assert texto.endswith('"Filho que só liga quando precisa?"')
    assert "[ACAO]" not in texto and "[BLOCO A]" not in texto


def test_prompt_com_acao_exige_acao():
    with pytest.raises(prompt_mod.PromptInvalido):
        prompt_mod.prompt_com_acao("   ")


def test_sets_de_hashtags_e_descricao_padrao():
    conjuntos = prompt_mod.sets_de_hashtags()
    assert set(conjuntos) >= {"A1", "A2", "A3"}
    assert conjuntos["A1"].startswith("#conselhodavovo") and "#ia" in conjuntos["A1"]
    descricao = prompt_mod.descricao_padrao("Feijão requentado nunca fica igual 🍲",
                                            conjuntos["A1"])
    assert "Feijão requentado nunca fica igual 🍲" in descricao
    assert "Manda teu caso aqui" in descricao          # CTA padrão
    assert "🤖 Vó criada com IA" in descricao          # rótulo exigido pelas plataformas
    assert "#vosemfiltro" in descricao
    assert "[LEGENDA]" not in descricao and "[SET]" not in descricao


def test_bloco_a_e_o_mesmo_do_template():
    assert prompt_mod.bloco_a().startswith("Vertical 9:16 smartphone-style video.")
    assert "rooster-shaped wall clock" in prompt_mod.bloco_a()


def test_referencias_do_roteiro_usa_as_folhas_padrao(tmp_path, monkeypatch):
    from src.vsf_produce import referencias as assets

    monkeypatch.setenv("VSF_ASSETS_DIR", str(tmp_path))
    (tmp_path / assets.FOLHA_PERSONAGEM).write_bytes(b"png")
    (tmp_path / assets.FOLHA_CENARIO).write_bytes(b"png")
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="p")
    caminhos = producao.referencias_do_roteiro(roteiro)
    assert [Path(c).name for c in caminhos] == [assets.FOLHA_PERSONAGEM,
                                                assets.FOLHA_CENARIO]
    assert all(Path(c).is_absolute() for c in caminhos)

    sem = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="p",
                       usar_referencias_padrao=False)
    assert producao.referencias_do_roteiro(sem) == []


def test_asset_ausente_explica_onde_ajustar(tmp_path, monkeypatch):
    from src.vsf_produce import referencias as assets

    monkeypatch.setenv("VSF_ASSETS_DIR", str(tmp_path))
    with pytest.raises(assets.AssetAusente) as erro:
        assets.referencias_padrao()
    assert "VSF_ASSETS_DIR" in str(erro.value)


def test_gerar_takes_usa_r2v_com_as_folhas_padrao(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("VSF_ASSETS_DIR", str(tmp_path / "assets"))
    pasta_assets = tmp_path / "assets"
    pasta_assets.mkdir()
    from src.vsf_produce import referencias as assets
    for nome in assets.PADRAO:
        (pasta_assets / nome).write_bytes(b"png")

    roteiro = spec.novo(tmp_path, slug="ref", semana=3, gancho="G", prompt="",
                        acao='She says: "Escuta a vó."', modelo="omni-flash",
                        duracao=10)
    producao.gerar_takes(roteiro, base=tmp_path, dry_run=True)
    saida = capsys.readouterr().out
    assert "video r2v" in saida                      # r2v, não t2v
    assert saida.count("--ref") == 2                 # folha de personagem + cenário
    assert "referências (2):" in saida
    assert "BLOCO A" not in roteiro.prompt and "Brazilian grandmother" in roteiro.prompt


def test_comando_melt_usa_vsf_melt_do_ambiente(monkeypatch):
    monkeypatch.setenv("VSF_MELT", "melt-x --extra")
    assert editor_mlt.comando_melt() == ["melt-x", "--extra"]


def test_deslocar_srt_adianta_pelo_tempo_cortado():
    srt = "1\n00:00:00,016 --> 00:00:00,933\nFeijão no\nfogo\n\n2\n00:00:02,000 --> 00:00:02,500\ndesde cedo\n"
    deslocado = editor_mlt.deslocar_srt(srt, 0.6)
    assert "00:00:00,000 --> 00:00:00,333" in deslocado      # 0,016-0,6 vira 0 (clamp)
    assert "00:00:01,400 --> 00:00:01,900" in deslocado      # 2,0-0,6
    assert "Feijão no" in deslocado and "desde cedo" in deslocado
    assert deslocado.count("-->") == 2


def test_deslocar_srt_descarta_cue_que_caiu_no_corte():
    srt = "1\n00:00:00,100 --> 00:00:00,500\ntrecho cortado\n\n2\n00:00:01,500 --> 00:00:02,000\nresto\n"
    deslocado = editor_mlt.deslocar_srt(srt, 0.8)
    assert "trecho cortado" not in deslocado
    assert "resto" in deslocado
    assert deslocado.startswith("1\n")                        # reindexado


def test_deslocar_srt_sem_offset_nao_mexe():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nOi\n"
    assert editor_mlt.deslocar_srt(srt, 0) == srt


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_projeto_desloca_a_legenda_para_o_tempo_da_timeline(tmp_path):
    """O filtro do MLT usa tempo de timeline: sem o deslocamento a legenda adianta."""
    clipe = _clipe_falso(tmp_path / "clipe.mp4", segundos=3.0)
    srt = "1\n00:00:01,200 --> 00:00:01,900\nOi\n"
    projeto = editor_mlt.montar_projeto(clipe, tmp_path / "p.mlt", gancho="G", srt=srt)
    xml = (tmp_path / "p.mlt").read_text(encoding="utf-8")
    assert projeto.inicio > 0
    assert "00:00:01,200" not in xml                          # tempo do clipe não fica
    deslocado = editor_mlt._escrever_ts_srt(1.2 - projeto.inicio)
    assert deslocado in xml                                   # desceu pelo corte exato


def test_gancho_e_legenda_nao_se_sobrepoem():
    """O defeito original: o gancho terminava em 1518 e cobria a legenda (base 1440)."""
    p = Presets()
    _, gy, _, gh, _ = p.gancho.geometry.split()
    _, cy, _, ch, _ = p.legenda.geometry.split()
    fundo_gancho = int(gy) + int(gh)
    fundo_legenda = int(cy) + int(ch)
    assert fundo_gancho < fundo_legenda
    # espaço para duas linhas de legenda abaixo do gancho
    assert fundo_gancho <= fundo_legenda - 2 * p.legenda.size


def test_prompt_do_frame_substitui_a_cena():
    texto = prompt_mod.prompt_do_frame("Ela aponta para a panela no fogão.")
    assert "Ela aponta para a panela no fogão." in texto
    assert "[CENA]" not in texto
    assert "relógio em formato de galo" in texto        # cenário preservado
    assert "Formato 9:16" in texto


def test_prompt_da_thumbnail_substitui_a_expressao():
    texto = prompt_mod.prompt_da_thumbnail("sarcástica, sobrancelha erguida")
    assert "sarcástica, sobrancelha erguida" in texto
    assert "[EXPRESSAO]" not in texto
    assert "70% do quadro" in texto


def test_prompt_do_video_traz_as_tags_de_ativo_do_flow():
    texto = prompt_mod.prompt_do_video(personagem="Vó Zulmira",
                                       referencia_quadro="quadro-v3.png")
    assert "Character: @Vó Zulmira" in texto
    assert "Voice: @Vó Zulmira" in texto
    assert "@vo-sem-filtro-character-sheet-2.png" in texto
    assert "quadro-v3.png" in texto
    assert "initial-frame-vX.png" not in texto          # placeholder substituído
    assert "No subtitles, no on-screen text, no watermark." in texto


def test_prompt_do_video_aceita_nome_com_arroba():
    texto = prompt_mod.prompt_do_video(personagem="@Vó Zulmira")
    assert "@@Vó Zulmira" not in texto and "Character: @Vó Zulmira" in texto


def test_prompt_sem_cena_avisa():
    with pytest.raises(prompt_mod.PromptInvalido):
        prompt_mod.prompt_do_frame("   ")


def test_template_sem_o_bloco_pedido_avisa(tmp_path):
    incompleto = tmp_path / "t.txt"
    incompleto.write_text("== Video ==\n\nnada aqui\n", encoding="utf-8")
    with pytest.raises(prompt_mod.PromptInvalido) as erro:
        prompt_mod.prompt_do_frame("cena", incompleto)
    assert "Initial Frame" in str(erro.value)


def test_gerar_takes_monta_o_prompt_a_partir_da_cena(tmp_path):
    roteiro = spec.novo(tmp_path, slug="cena", semana=3, gancho="G", prompt="",
                        cena="Ela mexe o feijão e reclama do genro.",
                        personagem="@Vó Zulmira", modelo="omni-flash", duracao=10)
    producao.gerar_takes(roteiro, base=tmp_path, dry_run=True)
    assert "Character: @Vó Zulmira" in roteiro.prompt
    assert roteiro.prompt.strip()           # prompt ficou registrado no objeto
    assert (tmp_path / "roteiros" / "cena.json").exists()
    assert (tmp_path / "roteiros" / "cena" / "takes").is_dir()


def test_gancho_longo_e_quebrado_em_duas_linhas():
    assert editor_mlt.quebrar_gancho("CHEIRO DE DOMINGO") == "CHEIRO DE\nDOMINGO"
    assert editor_mlt.quebrar_gancho("Filha, olha isso.") == "Filha,\nolha isso."


def test_gancho_curto_e_explicito_sao_preservados():
    assert editor_mlt.quebrar_gancho("Oi") == "Oi"
    assert editor_mlt.quebrar_gancho("JA QUEBRADO\nAQUI") == "JA QUEBRADO\nAQUI"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_silencio_inicial_e_cortado(tmp_path):
    clipe = _clipe_falso(tmp_path / "clipe.mp4", segundos=3.0)
    corte = editor_mlt.leading_silence(clipe, keep=0.10)
    assert 0.8 <= corte <= 1.0            # 1 s de silêncio menos o respiro de 100 ms


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_clipe_com_fala_no_inicio_nao_e_cortado(tmp_path):
    destino = tmp_path / "sem-silencio.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=2", "-f", "lavfi",
                    "-i", "color=c=red:s=720x1280:d=2", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(destino)],
                   check=True, capture_output=True)
    assert editor_mlt.leading_silence(destino) == 0.0


# --------------------------------------------------------------- projeto .mlt

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_projeto_mlt_tem_gancho_janela_e_legendas(tmp_path):
    clipe = _clipe_falso(tmp_path / "clipe.mp4")
    srt = "1\n00:00:00,500 --> 00:00:01,000\nOi\n"
    projeto = editor_mlt.montar_projeto(clipe, tmp_path / "p.mlt", gancho="CHEIRO DE DOMINGO",
                                        srt=srt, presets=Presets())
    xml = (tmp_path / "p.mlt").read_text(encoding="utf-8")
    assert '<filter id="gancho" in=' in xml            # janela do gancho
    assert ">CHEIRO DE\nDOMINGO<" in xml               # texto do gancho, 2 linhas
    assert 'family">Anton<' in xml and 'size">173<' in xml
    assert 'family">Montserrat Black<' in xml and 'size">105<' in xml
    # a legenda é embutida com o tempo já deslocado para o tempo da timeline
    assert editor_mlt._escrever_ts_srt(0.5 - projeto.inicio) in xml
    assert projeto.inicio > 0                          # silêncio inicial marcado no in=
    assert f'in="{editor_mlt.ts(projeto.inicio)}"' in xml


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
def test_projeto_escapa_caracteres_do_srt(tmp_path):
    clipe = _clipe_falso(tmp_path / "clipe.mp4", segundos=2.0)
    srt = "1\n00:00:00,000 --> 00:00:01,000\nA & B <viu>\n"
    editor_mlt.montar_projeto(clipe, tmp_path / "p.mlt", gancho="X", srt=srt)
    xml = (tmp_path / "p.mlt").read_text(encoding="utf-8")
    assert "A &amp; B &lt;viu&gt;" in xml              # XML válido, texto preservado


def test_projeto_usa_caminho_absoluto_do_clipe(tmp_path):
    """O melt roda no flatpak: caminho relativo faz o render sair em branco."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg ausente")
    clipe = _clipe_falso(tmp_path / "clipe.mp4", segundos=2.0)
    projeto = editor_mlt.montar_projeto(clipe, tmp_path / "p.mlt", gancho="G")
    xml = (tmp_path / "p.mlt").read_text(encoding="utf-8")
    assert str(clipe.resolve()) in xml
    assert 'resource">data/' not in xml and 'resource">.' not in xml
    assert projeto.entrada.is_absolute()


def test_projeto_recusa_clipe_inexistente(tmp_path):
    with pytest.raises(editor_mlt.ErroDeRender):
        editor_mlt.montar_projeto(tmp_path / "nao-existe.mp4", tmp_path / "p.mlt",
                                  gancho="G")


def test_render_falha_alto_quando_melt_nao_desenha_texto(tmp_path, monkeypatch):
    clipe = tmp_path / "clipe.mp4"
    clipe.write_bytes(b"nao importa")
    monkeypatch.setattr(editor_mlt, "texto_aplicado", lambda *a, **k: False)
    with pytest.raises(editor_mlt.SemServidorGrafico):
        editor_mlt.render(tmp_path / "p.mlt", tmp_path / "s.mp4", melter=["melt"])


# ------------------------------------------------------------------ roteiro

def test_aprovar_rebaixa_o_anterior(tmp_path):
    roteiro = spec.Roteiro(slug="s", semana=2, gancho="G", prompt="P",
                           takes=[spec.Take(arquivo="takes/take1.mp4"),
                                  spec.Take(arquivo="takes/take2.mp4")])
    roteiro.aprovar("takes/take1.mp4")
    roteiro.aprovar("takes/take2.mp4")
    assert roteiro.aprovado == "takes/take2.mp4"
    primeiros = next(t for t in roteiro.takes if t.arquivo == "takes/take1.mp4")
    assert primeiros.status == "rejeitado"


def test_rejeitar_limpa_aprovacao_e_guarda_motivo():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="G", prompt="P",
                           takes=[spec.Take(arquivo="takes/take1.mp4")])
    roteiro.aprovar("takes/take1.mp4")
    roteiro.rejeitar("takes/take1.mp4", "alucinou a mão")
    assert roteiro.aprovado is None
    assert roteiro.takes[0].motivos == ["alucinou a mão"]


def test_salvar_e_carregar_preserva_tudo(tmp_path):
    roteiro = spec.novo(tmp_path, slug="cheiro", semana=3, gancho="CHEIRO DE DOMINGO",
                        prompt="prompt", titulo="t", tags=["#shorts"],
                        modelo="omni-flash", duracao=10)
    roteiro.takes.append(spec.Take(arquivo="takes/take1.mp4"))
    roteiro.aprovar("takes/take1.mp4")
    spec.salvar(roteiro, tmp_path)
    lido = spec.carregar(spec.caminho_do_roteiro("cheiro", tmp_path))
    assert lido.aprovado == "takes/take1.mp4"
    assert lido.modelo == "omni-flash" and lido.duracao == 10
    assert lido.takes[0].status == "aprovado"


def test_slug_repetido_e_recusado(tmp_path):
    spec.novo(tmp_path, slug="a", semana=1, gancho="g", prompt="p")
    with pytest.raises(FileExistsError):
        spec.novo(tmp_path, slug="a", semana=1, gancho="g", prompt="p")


def test_nome_de_saida_segue_o_padrao_do_canal():
    roteiro = spec.Roteiro(slug="cheiro-de-domingo", semana=4, gancho="G", prompt="P")
    assert roteiro.nome_saida() == "vosemfiltro-semana4-cheiro-de-domingo.mp4"
    assert roteiro.pasta_saida().name == "Semana4"


def test_editar_sem_take_aprovado_avisa(tmp_path):
    roteiro = spec.novo(tmp_path, slug="s", semana=1, gancho="g", prompt="p")
    with pytest.raises(producao.ProducaoInvalida):
        producao.editar(roteiro, base=tmp_path)


def test_cli_registra_take_manual(tmp_path, monkeypatch, capsys):
    """Rota garantida sem o gflow: o clipe baixado à mão vira take do roteiro."""
    from src.vsf_produce import cli

    monkeypatch.setattr(spec, "BASE_PADRAO", tmp_path)
    monkeypatch.setenv("VSF_OUTPUT_DIR", str(tmp_path / "saida"))
    clipe = tmp_path / "baixado-do-flow.mp4"
    clipe.write_bytes(b"video")
    cli.main(["novo", "--slug", "manual", "--semana", "2", "--gancho", "G",
              "--prompt", "p"])
    assert cli.main(["take", "manual", str(clipe), "--aprovar"]) == 0
    lido = spec.carregar(spec.caminho_do_roteiro("manual", tmp_path))
    assert lido.aprovado == "takes/take1.mp4"
    assert lido.takes[0].origem == "manual"
    assert (spec.pasta_do_roteiro(lido, tmp_path) / "takes" / "take1.mp4").exists()
    assert "registrado (aprovado)" in capsys.readouterr().out


def test_cli_take_com_arquivo_inexistente_falha(tmp_path, monkeypatch, capsys):
    from src.vsf_produce import cli

    monkeypatch.setattr(spec, "BASE_PADRAO", tmp_path)
    cli.main(["novo", "--slug", "manual", "--semana", "2", "--gancho", "G",
              "--prompt", "p"])
    assert cli.main(["take", "manual", str(tmp_path / "nao-existe.mp4")]) == 2
    assert "não encontrado" in capsys.readouterr().err


# -------------------------------------------------------------- driver Flow

def test_custo_por_modelo_e_duracao():
    def roteiro(modelo: str, duracao: int, resolucao: str = "720p"):
        return spec.Roteiro(slug="s", semana=1, gancho="g", prompt="p", modelo=modelo,
                            duracao=duracao, resolucao=resolucao)

    assert flow_driver.custo_estimado(roteiro("veo-3.1-fast", 8)) == 20
    assert flow_driver.custo_estimado(roteiro("veo-3.1-lite", 8)) == 10
    assert flow_driver.custo_estimado(roteiro("veo-3.1-quality", 8)) == 100
    assert flow_driver.custo_estimado(roteiro("omni-flash", 10)) == 15
    assert flow_driver.custo_estimado(roteiro("omni-flash", 10, "360p")) == 7
    assert flow_driver.custo_estimado(roteiro("veo-3.1-fast", 8), 3) == 60


def test_duracao_10_so_no_omni_flash():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="p",
                           modelo="veo-3.1-fast", duracao=10)
    with pytest.raises(flow_driver.RoteiroInvalido):
        flow_driver.validar(roteiro)


def test_comando_usa_r2v_quando_ha_referencia_e_t2v_sem():
    sem = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="a vó fala", 
                       modelo="omni-flash", duracao=10)
    geracao = flow_driver.montar_comando(sem, Path("/tmp/take1.mp4"))
    assert geracao.comando[:2] == ["video", "t2v"]
    assert "--duration" in geracao.comando and "10" in geracao.comando
    assert "--resolution" in geracao.comando          # omni-flash aceita resolução
    assert geracao.custo == 15

    com = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="a vó fala",
                       modelo="veo-3.1-fast", referencias=["quadro.png"])
    comando = flow_driver.montar_comando(com, Path("/tmp/take2.mp4")).comando
    assert comando[:2] == ["video", "r2v"]
    assert "--ref" in comando and "quadro.png" in comando
    assert "--resolution" not in comando


def test_personagem_arroba_vai_no_prompt_e_id_vira_referencia():
    com_arroba = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="fala",
                              personagem="@Vó Zu")
    comando = flow_driver.montar_comando(com_arroba, Path("/tmp/t.mp4")).comando
    assert comando[2].endswith("@Vó Zu") and "--reference-entity" not in comando

    com_id = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="fala",
                          personagem="abc123")
    comando = flow_driver.montar_comando(com_id, Path("/tmp/t.mp4")).comando
    assert "--reference-entity" in comando and "abc123" in comando


def test_modelo_desconhecido_explica_as_opcoes():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="p", modelo="sora")
    with pytest.raises(flow_driver.RoteiroInvalido) as erro:
        flow_driver.modelo_do_roteiro(roteiro)
    assert "veo-fast" in str(erro.value)


class _Proc:
    """Resultado de subprocess falso para exercitar o tratamento de erro do driver."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_comando_recusa_roteiro_sem_prompt():
    """Sem prompt, o fallback de @personagem mandaria só a tag e gastaria crédito."""
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", prompt="",
                           personagem="@Vó Zulmira")
    with pytest.raises(flow_driver.RoteiroInvalido) as erro:
        flow_driver.montar_comando(roteiro, Path("/tmp/t.mp4"))
    assert "--cena" in str(erro.value)


def test_comando_nao_duplica_a_tag_quando_o_prompt_ja_traz():
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", personagem="@Vó Zulmira",
                           prompt="References:\nCharacter: @Vó Zulmira\n\nVertical 9:16 …")
    comando = flow_driver.montar_comando(roteiro, Path("/tmp/t.mp4")).comando
    assert comando[2].count("@Vó Zulmira") == 1


def test_sem_referencias_remove_o_bloco_de_tags():
    texto = prompt_mod.prompt_do_video(personagem="Vó Zulmira")
    limpo = prompt_mod.sem_referencias(texto)
    assert "@" not in limpo
    assert "References:" not in limpo
    assert "Vertical 9:16 smartphone-style video." in limpo


def test_gerar_recua_sem_mencoes_quando_o_gflow_nao_resolve(tmp_path, monkeypatch):
    """Conta flow.google.com: o gflow aborta por @menção — reenviar sem o bloco."""
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", personagem="@Vó Zulmira",
                           modelo="omni-flash", duracao=10, resolucao="720p",
                           prompt=prompt_mod.prompt_do_video())
    prompts, destino = [], tmp_path / "take1.mp4"

    def falso(args, **kwargs):
        prompts.append(args[2])
        if len(prompts) == 1:
            return _Proc(3, stdout='{"error_class": "MentionIndexUnavailableError", '
                                   '"detail": "the character source is unavailable"}')
        destino.write_bytes(b"video")
        return _Proc(0, stdout="{}")

    monkeypatch.setattr(flow_driver, "_rodar", falso)
    assert flow_driver.gerar(roteiro, destino).exists()
    assert len(prompts) == 2
    assert "@Vó Zulmira" in prompts[0]
    assert "@" not in prompts[1]
    assert "Vertical 9:16 smartphone-style video." in prompts[1]


def test_gerar_tira_refs_e_tags_quando_a_conta_nao_carrega_o_catalogo(tmp_path, monkeypatch):
    """Sem catálogo de ativos o gflow recusa --ref e @tags: cai para t2v puro."""
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", modelo="omni-flash",
                           duracao=10, referencias=["/tmp/folha.png"],
                           prompt="References:\nCharacter: @Vó Zulmira\n\nVertical 9:16 fala.")
    comandos, destino = [], tmp_path / "take1.mp4"

    def falso(args, **kwargs):
        comandos.append(list(args))
        if len(comandos) < 2:
            return _Proc(29, stdout='{"error_class": "MentionIndexUnavailableError", '
                                    '"detail": "the character source is unavailable"}')
        destino.write_bytes(b"video")
        return _Proc(0, stdout="{}")

    monkeypatch.setattr(flow_driver, "_rodar", falso)
    assert flow_driver.gerar(roteiro, destino).exists()
    assert comandos[0][1] == "r2v" and "--ref" in comandos[0]
    assert comandos[1][1] == "t2v" and "--ref" not in comandos[1]
    assert "@" not in comandos[1][2]
    assert "Vertical 9:16 fala." in comandos[1][2]


def test_gerar_nao_insiste_em_erro_de_outra_natureza(tmp_path, monkeypatch):
    roteiro = spec.Roteiro(slug="s", semana=1, gancho="g", modelo="omni-flash",
                           duracao=10, referencias=["/tmp/folha.png"], prompt="fala")
    chamadas = []

    def falso(args, **kwargs):
        chamadas.append(list(args))
        return _Proc(28, stdout='{"error_class": "UiModeUnavailable"}')

    monkeypatch.setattr(flow_driver, "_rodar", falso)
    with pytest.raises(flow_driver.FlowIndisponivel):
        flow_driver.gerar(roteiro, tmp_path / "take1.mp4")
    assert len(chamadas) == 1          # não repete tentativa inútil


def test_saldo_indisponivel_avisa_em_vez_de_culpar_a_sessao(monkeypatch):
    """Contas servidas por flow.google.com não expõem saldo — erro do gflow #795."""
    monkeypatch.setattr(flow_driver, "_rodar", lambda *a, **k: _Proc(
        3, stdout='{"class": "AisandboxAuthError", '
                   '"detail": "the labs.google session returned no access token"}'))
    with pytest.raises(flow_driver.SaldoIndisponivel) as erro:
        flow_driver.creditos()
    assert "flow.google.com" in str(erro.value)
    assert "geração funciona" in str(erro.value)


def test_sessao_morta_continua_sendo_flow_indisponivel(monkeypatch):
    monkeypatch.setattr(flow_driver, "_rodar", lambda *a, **k: _Proc(1, stderr="unauthorized"))
    with pytest.raises(flow_driver.FlowIndisponivel):
        flow_driver.creditos()


def test_sessao_ok_usa_auth_status_e_nao_depende_do_saldo(monkeypatch):
    chamadas = []

    def falso(args, **kwargs):
        chamadas.append(args)
        return _Proc(0, stdout="Flow session verified as user@example.com.")

    monkeypatch.setattr(flow_driver, "_rodar", falso)
    assert flow_driver.sessao_ok() is True
    assert chamadas[0][:2] == ["auth", "status"]      # nunca `credits`
    monkeypatch.setattr(flow_driver, "_rodar", lambda *a, **k: _Proc(1))
    assert flow_driver.sessao_ok() is False


def test_cli_creditos_de_conta_flow_google_sai_com_sucesso(tmp_path, monkeypatch, capsys):
    from src.vsf_produce import cli

    monkeypatch.setattr(flow_driver, "_rodar", lambda *a, **k: _Proc(
        3, stdout='{"class": "AisandboxAuthError", "detail": "no access token"}'))
    assert cli.main(["creditos"]) == 0
    assert "aviso:" in capsys.readouterr().out


# ------------------------------------------------------- render real (lento)

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg ausente")
@pytest.mark.skipif(shutil.which("flatpak") is None and shutil.which("melt") is None,
                    reason="melt/Shotcut ausente")
def test_render_real_produz_mp4_vertical(tmp_path):
    """Render de ponta a ponta: exige motor de texto (senão falha explícita)."""
    clipe = _clipe_falso(tmp_path / "clipe.mp4")
    projeto = editor_mlt.montar_projeto(clipe, tmp_path / "p.mlt", gancho="TESTE",
                                        srt="1\n00:00:01,000 --> 00:00:02,000\nOi\n")
    saida = tmp_path / "saida.mp4"
    try:
        editor_mlt.render(projeto, saida)
    except editor_mlt.SemServidorGrafico:
        pytest.skip("ambiente sem display para os filtros de texto do Shotcut")
    assert saida.stat().st_size > 10_000
    dimensoes = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(saida)],
        capture_output=True, text=True).stdout.strip()
    assert dimensoes == "1080,1920"
