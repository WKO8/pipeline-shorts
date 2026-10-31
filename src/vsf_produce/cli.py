"""CLI da produção de Shorts da Vó Sem Filtro.

    python -m src.vsf_produce.cli novo --slug cheiro-de-domingo --semana 3 \\
        --gancho "CHEIRO DE DOMINGO" --prompt "..." --titulo "..."
    python -m src.vsf_produce.cli gerar cheiro-de-domingo --quantidade 2
    python -m src.vsf_produce.cli aprovar cheiro-de-domingo take2.mp4
    python -m src.vsf_produce.cli editar cheiro-de-domingo
    python -m src.vsf_produce.cli publicar cheiro-de-domingo --privacidade private

A página local (`./run_ui.sh`) expõe as mesmas ações com revisão dos takes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import flow_driver, producao, spec
from .editor_mlt import ErroDeRender, SemServidorGrafico


def _roteiro(slug: str) -> spec.Roteiro:
    caminho = spec.caminho_do_roteiro(slug)
    if not caminho.exists():
        raise SystemExit(f"roteiro {slug!r} não existe ({caminho})")
    return spec.carregar(caminho)


def _cmd_novo(args: argparse.Namespace) -> int:
    from . import prompt as prompt_mod

    if not (args.prompt or args.cena or args.acao):
        print("erro: informe --acao (BLOCO A + a fala), --cena ou --prompt",
              file=sys.stderr)
        return 2
    descricao = args.descricao
    if args.legenda:
        conjuntos = prompt_mod.sets_de_hashtags()
        hashtags: str = ""
        if args.set:
            hashtags = conjuntos.get(args.set.upper()) or args.set
        descricao = prompt_mod.descricao_padrao(args.legenda, hashtags)
    roteiro = spec.novo(slug=args.slug, semana=args.semana, gancho=args.gancho,
                        prompt=args.prompt or "", titulo=args.titulo or args.gancho,
                        descricao=descricao, tags=args.tags or [],
                        preset=args.preset, modelo=args.modelo, duracao=args.duracao,
                        personagem=args.personagem,
                        referencias=args.referencia or [],
                        usar_referencias_padrao=not args.sem_referencias_padrao,
                        cena=args.cena or "", expressao=args.expressao or "",
                        acao=args.acao or "",
                        referencia_quadro=args.referencia_quadro,
                        resolucao=args.resolucao,
                        gancho_segundos=args.gancho_segundos)
    print(f"roteiro criado: {spec.caminho_do_roteiro(roteiro.slug)}")
    print(f"custo estimado por take: {flow_driver.custo_estimado(roteiro)} créditos")
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    """Mostra os prompts montados pelo template base — não gasta nada."""
    from . import prompt as prompt_mod

    roteiro = _roteiro(args.slug)
    if args.bloco == "video":
        print(producao.montar_prompt_do_roteiro(roteiro))
    elif args.bloco == "frame":
        if not roteiro.cena:
            print("este roteiro não tem --cena definida", file=sys.stderr)
            return 2
        print(prompt_mod.prompt_do_frame(roteiro.cena, roteiro.prompt_template))
    else:
        if not roteiro.expressao:
            print("este roteiro não tem --expressao definida", file=sys.stderr)
            return 2
        print(prompt_mod.prompt_da_thumbnail(roteiro.expressao, roteiro.prompt_template))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    linhas = producao.resumo()
    if args.json:
        print(json.dumps(linhas, ensure_ascii=False, indent=2))
        return 0
    if not linhas:
        print("nenhum roteiro em data/vsf_produce/roteiros/")
        return 0
    for item in linhas:
        pendentes = sum(1 for t in item["takes"] if t["status"] == "pendente")
        print(f"[{item['slug']}] semana {item['semana']} · {item['modelo']} "
              f"{item['duracao']}s · {item['custo_por_take']} créditos/take")
        print(f"   gancho: {item['gancho']!r}")
        print(f"   takes: {len(item['takes'])} ({pendentes} pendentes) · "
              f"aprovado: {item['aprovado'] or '—'}")
        print(f"   legenda: {item['legenda'] or '—'} · editado: {item['editado'] or '—'}")
    return 0


def _cmd_creditos(args: argparse.Namespace) -> int:
    try:
        saldo = flow_driver.creditos(args.profile)
    except flow_driver.SaldoIndisponivel as aviso:
        print(f"aviso: {aviso}")
        return 0
    except flow_driver.FlowIndisponivel as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print(json.dumps(saldo, ensure_ascii=False, indent=2))
    return 0


def _cmd_sessao(args: argparse.Namespace) -> int:
    ok = flow_driver.sessao_ok(args.profile)
    print("sessão do Flow:", "verificada" if ok else "AUSENTE ou inválida")
    if not ok:
        print("   rode: gflow auth login --browser chrome --profile default --account <email>")
        return 1
    return 0


def _cmd_motor(args: argparse.Namespace) -> int:
    ok = producao.verificar_motor()
    print("filtros de texto do Shotcut (melt):", "OK" if ok else "INDISPONÍVEIS")
    if not ok:
        print("   o render sairia sem gancho e sem legendas. Use uma sessão gráfica ou "
              "instale o xvfb e rode com `xvfb-run -a`.")
        return 1
    return 0


def _cmd_gerar(args: argparse.Namespace) -> int:
    roteiro = _roteiro(args.slug)
    producao.gerar_takes(roteiro, quantidade=args.quantidade, profile=args.profile,
                         projeto=args.projeto, dry_run=args.dry_run)
    return 0


def _cmd_aprovar(args: argparse.Namespace) -> int:
    roteiro = _roteiro(args.slug)
    take = roteiro.aprovar(args.take)
    spec.salvar(roteiro)
    print(f"aprovado: {take.arquivo}")
    return 0


def _cmd_rejeitar(args: argparse.Namespace) -> int:
    roteiro = _roteiro(args.slug)
    take = roteiro.rejeitar(args.take, args.motivo or "")
    spec.salvar(roteiro)
    print(f"rejeitado: {take.arquivo}" + (f" — {args.motivo}" if args.motivo else ""))
    if args.regerar:
        args.quantidade = 1
        return _cmd_gerar(args)
    return 0


def _cmd_editar(args: argparse.Namespace) -> int:
    roteiro = _roteiro(args.slug)
    saida = producao.editar(roteiro, srt=Path(args.srt) if args.srt else None)
    print(f"editado: {saida}")
    return 0


def _cmd_publicar(args: argparse.Namespace) -> int:
    from .publisher import PublicacaoFalhou, link

    roteiro = _roteiro(args.slug)
    if args.dry_run:
        video = Path(args.video) if args.video else \
            roteiro.pasta_saida() / roteiro.nome_saida()
        print(f"publicaria {video} como {roteiro.titulo!r} "
              f"(privacidade={args.privacidade}, agendar={args.agendar or '—'})")
        return 0
    try:
        resposta = producao.publicar(roteiro, Path(args.video) if args.video else None,
                                     privacidade=args.privacidade,
                                     publicar_em=args.agendar)
    except PublicacaoFalhou as erro:
        print(f"falha ao publicar: {erro}")
        return 1
    identificador = resposta.get("id", "?")
    print(f"publicado: {link(identificador)}")
    return 0


def _cmd_take(args: argparse.Namespace) -> int:
    """Registra um clipe já baixado (gerado à mão no Flow) como take do roteiro."""
    import shutil

    roteiro = _roteiro(args.slug)
    origem = Path(args.arquivo).expanduser()
    if not origem.exists():
        print(f"arquivo não encontrado: {origem}", file=sys.stderr)
        return 2
    pasta = spec.pasta_do_roteiro(roteiro) / "takes"
    pasta.mkdir(parents=True, exist_ok=True)
    numero = len(roteiro.takes) + 1
    destino = pasta / f"take{numero}{origem.suffix or '.mp4'}"
    (shutil.move if args.mover else shutil.copy2)(str(origem), str(destino))
    take = spec.Take(arquivo=f"takes/{destino.name}", origem="manual",
                     modelo=args.modelo or roteiro.modelo)
    if args.aprovar:
        roteiro.takes.append(take)
        roteiro.aprovar(take.arquivo)
    else:
        roteiro.takes.append(take)
    spec.salvar(roteiro)
    estado = "aprovado" if args.aprovar else "pendente"
    print(f"{take.arquivo} registrado ({estado})"
          + (" — origem movida" if args.mover else ""))
    return 0


def _cmd_tiktok_login(args: argparse.Namespace) -> int:
    from .tiktok_driver import LoginNecessario, login, perfil

    print(f"perfil dedicado: {perfil()}")
    try:
        url = login(args.timeout)
    except LoginNecessario as erro:
        print(f"não deu: {erro}", file=sys.stderr)
        return 1
    print(f"ok — sessão salva; upload page: {url}")
    return 0


def _cmd_tiktok(args: argparse.Namespace) -> int:
    from .tiktok_driver import LoginNecessario, TikTokIndisponivel, perfil, subir

    roteiro = _roteiro(args.slug)
    video = Path(args.video) if args.video else \
        roteiro.pasta_saida() / roteiro.nome_saida()
    legenda = args.legenda if args.legenda is not None else producao.legenda_tiktok(roteiro)
    if not video.exists():
        print(f"vídeo não encontrado: {video} (rode `editar` antes)", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"subiria {video}\n  ação: {args.acao}\n  legenda:\n{legenda}\n"
              f"  perfil: {perfil()}")
        return 0
    try:
        resultado = subir(video, legenda=legenda, acao=args.acao)
    except LoginNecessario as erro:
        print(f"login necessário: {erro}", file=sys.stderr)
        return 1
    except TikTokIndisponivel as erro:
        print(f"falha no TikTok: {erro}", file=sys.stderr)
        return 1
    detalhe = "privado" if resultado.privado else "ATENÇÃO: visibilidade não confirmada"
    print(f"tiktok: {resultado.acao} concluído ({detalhe}) · {resultado.url}")
    if resultado.screenshot:
        print(f"captura: {resultado.screenshot}")
    return 0


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vsf-produce", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="comando", required=True)

    novo = sub.add_parser("novo", help="cria um roteiro")
    novo.add_argument("--slug", required=True)
    novo.add_argument("--semana", type=int, required=True)
    novo.add_argument("--gancho", required=True, help="texto que aparece na tela")
    novo.add_argument("--prompt", default="",
                      help="prompt literal para o Flow (alternativa a --cena)")
    novo.add_argument("--cena", default="",
                      help="o que acontece neste vídeo; monta o prompt pelo template base")
    novo.add_argument("--acao", default="",
                      help="fala/gesto em inglês; o prompt vira BLOCO A + ação (forma do canal)")
    novo.add_argument("--legenda", default="",
                      help="legenda do post; monta a descrição padrão do template")
    novo.add_argument("--set", default="", help="conjunto de hashtags do template (A1, A2, A3)")
    novo.add_argument("--sem-referencias-padrao", action="store_true",
                      help="não usar a folha de personagem/cenário como referência de imagem")
    novo.add_argument("--expressao", default="", help="expressão da thumbnail")
    novo.add_argument("--referencia-quadro", default=None,
                      help="nome do quadro inicial no projeto (substitui initial-frame-vX.png)")
    novo.add_argument("--titulo", default="")
    novo.add_argument("--descricao", default="")
    novo.add_argument("--tags", nargs="*")
    novo.add_argument("--preset", default="anton", choices=sorted(spec.PRESETS))
    novo.add_argument("--modelo", default="veo-3.1-fast")
    novo.add_argument("--duracao", type=int, default=8, choices=[4, 6, 8, 10])
    novo.add_argument("--resolucao", default="720p", choices=["360p", "720p"],
                      help="só o omni-flash aceita escolher; nos Veo é sempre 720p")
    novo.add_argument("--gancho-segundos", type=float, default=2.4)
    novo.add_argument("--personagem", default="@Vó Zulmira",
                      help="nome do Character no Flow (@Nome) ou id da entidade")
    novo.add_argument("--referencia", action="append",
                      help="imagem de referência (repetível; usa r2v)")
    novo.set_defaults(func=_cmd_novo)

    prompt_cmd = sub.add_parser("prompt", help="mostra o prompt montado (não gasta nada)")
    prompt_cmd.add_argument("slug")
    prompt_cmd.add_argument("--bloco", default="video",
                            choices=["video", "frame", "thumbnail"])
    prompt_cmd.set_defaults(func=_cmd_prompt)

    status = sub.add_parser("status", help="resumo dos roteiros e takes")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=_cmd_status)

    creditos = sub.add_parser("creditos", help="saldo de créditos do Flow (pode não existir na sua conta)")
    creditos.add_argument("--profile", default=None)
    creditos.set_defaults(func=_cmd_creditos)

    sessao = sub.add_parser("sessao", help="confere se a sessão do Flow está válida")
    sessao.add_argument("--profile", default=None)
    sessao.set_defaults(func=_cmd_sessao)

    motor = sub.add_parser("motor", help="testa se o melt consegue desenhar texto")
    motor.set_defaults(func=_cmd_motor)

    gerar = sub.add_parser("gerar", help="gera takes no Flow")
    gerar.add_argument("slug")
    gerar.add_argument("--quantidade", type=int, default=1, choices=[1, 2, 3, 4])
    gerar.add_argument("--profile", default=None)
    gerar.add_argument("--projeto", default=None)
    gerar.add_argument("--dry-run", action="store_true",
                       help="mostra o comando e o custo sem gastar crédito")
    gerar.set_defaults(func=_cmd_gerar, take=None)

    aprovar = sub.add_parser("aprovar", help="aprova um take para edição")
    aprovar.add_argument("slug")
    aprovar.add_argument("take")
    aprovar.set_defaults(func=_cmd_aprovar)

    take = sub.add_parser("take", help="registra um clipe do Flow baixado à mão")
    take.add_argument("slug")
    take.add_argument("arquivo", help="caminho do clipe gerado no Flow")
    take.add_argument("--aprovar", action="store_true",
                      help="já marca como aprovado (pula a revisão)")
    take.add_argument("--mover", action="store_true",
                      help="move em vez de copiar (padrão: copia)")
    take.add_argument("--modelo", default=None)
    take.set_defaults(func=_cmd_take)

    rejeitar = sub.add_parser("rejeitar", help="rejeita um take (e opcionalmente regera)")
    rejeitar.add_argument("slug")
    rejeitar.add_argument("take")
    rejeitar.add_argument("--motivo", default="")
    rejeitar.add_argument("--regerar", action="store_true",
                          help="gera um take novo na sequência")
    rejeitar.add_argument("--quantidade", type=int, default=1)
    rejeitar.add_argument("--dry-run", action="store_true")
    rejeitar.set_defaults(func=_cmd_rejeitar, profile=None, projeto=None)

    editar = sub.add_parser("editar", help="renderiza o take aprovado")
    editar.add_argument("slug")
    editar.add_argument("--srt", default=None)
    editar.set_defaults(func=_cmd_editar)

    publicar = sub.add_parser("publicar", help="sobe o vídeo editado para o YouTube")
    publicar.add_argument("slug")
    publicar.add_argument("--video", default=None)
    publicar.add_argument("--privacidade", default="private",
                          choices=["private", "public", "unlisted"])
    publicar.add_argument("--agendar", default=None, help="ISO-8601 UTC")
    publicar.add_argument("--dry-run", action="store_true")
    publicar.set_defaults(func=_cmd_publicar)

    tiktok_login = sub.add_parser("tiktok-login",
                                  help="login único no TikTok (perfil dedicado do Playwright)")
    tiktok_login.add_argument("--timeout", type=int, default=420)
    tiktok_login.set_defaults(func=_cmd_tiktok_login)

    tiktok = sub.add_parser("tiktok", help="sobe o vídeo editado no TikTok Studio")
    tiktok.add_argument("slug")
    tiktok.add_argument("--video", default=None)
    tiktok.add_argument("--acao", default="privado", choices=["privado", "rascunho"],
                        help="privado = publica como 'Somente você'; rascunho = só preenche")
    tiktok.add_argument("--legenda", default=None)
    tiktok.add_argument("--dry-run", action="store_true")
    tiktok.set_defaults(func=_cmd_tiktok)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ErroDeRender, SemServidorGrafico, producao.ProducaoInvalida,
            flow_driver.FlowIndisponivel, flow_driver.SaldoIndisponivel,
            flow_driver.RoteiroInvalido, FileExistsError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
