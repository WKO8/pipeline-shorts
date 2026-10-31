"""Aba/API de produção de Shorts da Vó Sem Filtro (roteiro → Flow → aprovação → edição).

Página servida em ``/vsf-produce`` pelo mesmo Flask do pipeline (``./run_ui.sh``).
Toda ação pesada (gerar no Flow, renderizar, publicar) roda em thread separada e pode
ser acompanhada em ``/api/vsf/tarefa/<id>`` — a página faz o polling e mostra o log,
para a requisição HTTP nunca ficar pendurada por minutos.
"""

from __future__ import annotations

import contextlib
import io
import threading
import uuid
from pathlib import Path

from flask import Blueprint, jsonify, request, send_from_directory

from src.vsf_produce import flow_driver, producao, spec
from src.vsf_produce.editor_mlt import ErroDeRender, SemServidorGrafico

TAREFAS: dict[str, dict] = {}
_LOCK = threading.Lock()


def _rodar_em_thread(nome: str, funcao) -> str:
    """Executa `funcao` capturando stdout/stderr para o painel de log."""
    identificador = uuid.uuid4().hex[:8]
    with _LOCK:
        TAREFAS[identificador] = {"nome": nome, "estado": "rodando", "log": ""}

    def alvo() -> None:
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
                resultado = funcao()
            estado, extra = "ok", {"resultado": str(resultado) if resultado else ""}
        except Exception as erro:  # a thread não pode morrer em silêncio
            estado, extra = "erro", {"erro": f"{type(erro).__name__}: {erro}"}
        with _LOCK:
            TAREFAS[identificador].update(estado=estado, log=buffer.getvalue(), **extra)

    threading.Thread(target=alvo, name=f"vsf-{nome}-{identificador}", daemon=True).start()
    return identificador


def register_vsf_produce(app, backend) -> None:
    """Registra a API e a página. `backend` é o módulo ui.app (PROJECT_ROOT/DATA_DIR)."""
    import os

    os.environ.setdefault("VSF_BASE_DIR", str(Path(backend.DATA_DIR) / "vsf_produce"))
    base = Path(os.environ["VSF_BASE_DIR"])
    bp = Blueprint("vsf_produce", __name__, url_prefix="/api/vsf")

    def roteiro_de(slug: str) -> spec.Roteiro:
        caminho = spec.caminho_do_roteiro(slug, base)
        if not caminho.exists():
            raise FileNotFoundError(f"roteiro {slug!r} não existe")
        return spec.carregar(caminho)

    # ------------------------------------------------------------- leitura
    @bp.get("/roteiros")
    def listar_roteiros():
        return jsonify({"base": str(base), "roteiros": producao.resumo(base)})

    @bp.get("/roteiro/<slug>")
    def detalhe_roteiro(slug: str):
        roteiro = roteiro_de(slug)
        return jsonify({
            "roteiro": {**roteiro.__dict__, "takes": [t.__dict__ for t in roteiro.takes]},
            "custo_por_take": flow_driver.custo_estimado(roteiro),
            "pasta": str(spec.pasta_do_roteiro(roteiro, base)),
            "saida": str(roteiro.pasta_saida() / roteiro.nome_saida()),
        })

    @bp.get("/creditos")
    def creditos():
        try:
            return jsonify({"ok": True, "creditos": flow_driver.creditos()})
        except flow_driver.SaldoIndisponivel as aviso:
            return jsonify({"ok": False, "aviso": str(aviso)})
        except flow_driver.FlowIndisponivel as erro:
            return jsonify({"ok": False, "erro": str(erro)})

    @bp.get("/midia/<slug>/<path:arquivo>")
    def midia(slug: str, arquivo: str):
        pasta = spec.pasta_do_roteiro(roteiro_de(slug), base)
        return send_from_directory(pasta, arquivo, conditional=True)

    @bp.get("/tarefa/<identificador>")
    def tarefa(identificador: str):
        with _LOCK:
            dados = TAREFAS.get(identificador)
        if not dados:
            return jsonify({"erro": "tarefa desconhecida"}), 404
        return jsonify({"id": identificador, **dados})

    # ------------------------------------------------------------- ações
    @bp.post("/roteiro")
    def salvar_roteiro():
        corpo = request.get_json(force=True) or {}
        slug = (corpo.get("slug") or "").strip()
        if not slug:
            return jsonify({"erro": "slug obrigatório"}), 400
        if spec.caminho_do_roteiro(slug, base).exists():
            roteiro = roteiro_de(slug)
            for campo in ("gancho", "prompt", "titulo", "descricao", "preset", "modelo",
                          "duracao", "resolucao", "personagem", "legenda_srt"):
                if campo in corpo and corpo[campo] not in (None, ""):
                    setattr(roteiro, campo, corpo[campo])
            if "gancho_segundos" in corpo:
                roteiro.gancho_segundos = float(corpo["gancho_segundos"])
        else:
            roteiro = spec.Roteiro(slug=slug, semana=int(corpo.get("semana") or 1),
                                   gancho=corpo.get("gancho") or "", prompt=corpo.get("prompt") or "")
            for campo in ("titulo", "descricao", "preset", "modelo", "resolucao",
                          "personagem", "legenda_srt"):
                if corpo.get(campo):
                    setattr(roteiro, campo, corpo[campo])
            roteiro.duracao = int(corpo.get("duracao") or 8)
            roteiro.tags = list(corpo.get("tags") or [])
        spec.salvar(roteiro, base)
        return jsonify({"ok": True, "slug": roteiro.slug})

    @bp.post("/gerar")
    def gerar():
        corpo = request.get_json(force=True) or {}
        slug = corpo.get("slug")
        quantidade = int(corpo.get("quantidade") or 1)
        dry = bool(corpo.get("dry_run"))

        def acao():
            roteiro = roteiro_de(str(slug))
            flow_driver.validar(roteiro)
            if dry:
                geracao = flow_driver.montar_comando(
                    roteiro, spec.pasta_do_roteiro(roteiro, base) / "takes" / "takeN.mp4")
                return (" ".join(geracao.comando)
                        + f"\ncusto estimado: {geracao.custo} créditos (nada foi gasto)")
            producao.gerar_takes(roteiro, base=base, quantidade=quantidade)
            return "takes gerados"

        return jsonify({"id": _rodar_em_thread("gerar", acao)})

    @bp.post("/aprovar")
    def aprovar():
        corpo = request.get_json(force=True) or {}
        roteiro = roteiro_de(corpo["slug"])
        roteiro.aprovar(corpo["take"])
        spec.salvar(roteiro, base)
        return jsonify({"ok": True, "aprovado": roteiro.aprovado})

    @bp.post("/rejeitar")
    def rejeitar():
        corpo = request.get_json(force=True) or {}
        roteiro = roteiro_de(corpo["slug"])
        roteiro.rejeitar(corpo["take"], corpo.get("motivo") or "")
        spec.salvar(roteiro, base)
        if corpo.get("regerar"):
            def acao():
                producao.gerar_takes(roteiro_de(corpo["slug"]), base=base, quantidade=1)
                return "take novo gerado"

            return jsonify({"ok": True, "id": _rodar_em_thread("regerar", acao)})
        return jsonify({"ok": True})

    @bp.post("/editar")
    def editar():
        corpo = request.get_json(force=True) or {}
        slug = corpo["slug"]

        def acao():
            caminho = producao.editar(roteiro_de(slug), base=base)
            return f"editado em {caminho}"

        return jsonify({"id": _rodar_em_thread("editar", acao)})

    @bp.post("/publicar")
    def publicar():
        corpo = request.get_json(force=True) or {}
        slug = corpo["slug"]

        def acao():
            resposta = producao.publicar(roteiro_de(slug),
                                         privacidade=corpo.get("privacidade") or "private",
                                         publicar_em=corpo.get("agendar") or None)
            return f"https://www.youtube.com/watch?v={resposta.get('id', '?')}"

        return jsonify({"id": _rodar_em_thread("publicar", acao)})

    @bp.get("/motor")
    def motor():
        return jsonify({"texto_ok": producao.verificar_motor()})

    app.register_blueprint(bp)

    # ------------------------------------------------------------- página
    @app.get("/vsf-produce")
    def pagina_vsf_produce():
        return PAGINA


PAGINA = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vó Sem Filtro — produção</title>
<style>
 :root { --bg:#0f1117; --card:#171a23; --borda:#262b38; --txt:#e8eaf0; --fraco:#9aa3b2;
         --ok:#2ecc71; --erro:#e74c3c; --azul:#3d7eff; }
 * { box-sizing:border-box; }
 body { margin:0; background:var(--bg); color:var(--txt);
        font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
 header { padding:14px 20px; border-bottom:1px solid var(--borda); display:flex;
          gap:16px; align-items:center; flex-wrap:wrap; }
 h1 { font-size:17px; margin:0; }
 main { padding:18px 20px 60px; max-width:1180px; margin:0 auto; }
 .card { background:var(--card); border:1px solid var(--borda); border-radius:12px;
         padding:16px; margin-bottom:16px; }
 .linha { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
 .takes { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:12px;
          margin-top:12px; }
 .take { background:#11141c; border:1px solid var(--borda); border-radius:10px; padding:8px; }
 .take.aprovado { border-color:var(--ok); }
 .take.rejeitado { opacity:.5; }
 video { width:100%; border-radius:8px; background:#000; }
 button { background:var(--azul); color:#fff; border:0; border-radius:8px; padding:7px 12px;
          cursor:pointer; font-size:13px; }
 button.sec { background:#2b3140; }
 button.morto { background:#3a2b2b; }
 .fraco { color:var(--fraco); font-size:12px; }
 input, textarea, select { background:#0d1017; color:var(--txt); border:1px solid var(--borda);
          border-radius:8px; padding:7px 9px; font-size:13px; width:100%; }
 label { font-size:12px; color:var(--fraco); display:block; margin:8px 0 3px; }
 pre { background:#0b0e14; border:1px solid var(--borda); border-radius:8px; padding:10px;
       max-height:260px; overflow:auto; font-size:12px; white-space:pre-wrap; }
 .pill { background:#20262f; border-radius:999px; padding:3px 9px; font-size:12px; }
</style></head>
<body>
<header>
  <h1>👵🔥 Produção Vó Sem Filtro</h1>
  <span class="pill" id="saldo">créditos: —</span>
  <span class="pill" id="motor">motor: —</span>
  <button class="sec" onclick="carregar()">Atualizar</button>
</header>
<main>
  <div class="card">
    <div class="linha">
      <strong>Novo roteiro</strong>
      <span class="fraco">o gancho é o texto na tela; o prompt vai para o Flow</span>
    </div>
    <div class="linha" style="margin-top:8px">
      <div style="flex:1;min-width:150px"><label>slug</label>
        <input id="f-slug" placeholder="cheiro-de-domingo"></div>
      <div style="width:90px"><label>semana</label><input id="f-semana" type="number" value="3"></div>
    </div>
    <div class="linha">
      <div style="flex:1;min-width:220px"><label>gancho (texto na tela)</label>
        <input id="f-gancho" placeholder="CHEIRO DE DOMINGO"></div>
    </div>
    <label>prompt do Flow</label>
    <textarea id="f-prompt" rows="4" placeholder="Vertical 9:16 ... Vó Zu ..."></textarea>
    <div class="linha">
      <div style="flex:1"><label>título do YouTube</label><input id="f-titulo"></div>
      <div style="width:150px"><label>modelo</label>
        <select id="f-modelo">
          <option value="veo-3.1-fast">veo-3.1-fast (20)</option>
          <option value="veo-3.1-lite">veo-3.1-lite (10)</option>
          <option value="veo-3.1-quality">veo-3.1-quality (100)</option>
          <option value="omni-flash">omni-flash (7-15)</option>
        </select></div>
      <div style="width:90px"><label>duração</label>
        <select id="f-duracao"><option>4</option><option>6</option>
        <option selected>8</option><option>10</option></select></div>
      <div style="width:120px"><label>preset</label>
        <select id="f-preset"><option value="anton">anton</option>
        <option value="painel">painel</option></select></div>
      <div style="width:150px"><label>personagem</label>
        <input id="f-personagem" placeholder="@Vó Zu ou id"></div>
      <button style="margin-top:18px" onclick="salvarRoteiro()">Salvar</button>
    </div>
  </div>

  <div id="roteiros"></div>
  <div class="card"><strong>Log</strong><pre id="log">—</pre></div>
</main>
<script>
const $ = (id) => document.getElementById(id);
const logar = (t) => { $('log').textContent = t; };

async function api(url, opcoes) {
  const r = await fetch(url, opcoes);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
async function post(url, corpo) {
  return api(url, {method:'POST', headers:{'Content-Type':'application/json'},
                   body: JSON.stringify(corpo)});
}
async function acompanhar(id) {
  for (;;) {
    await new Promise(r => setTimeout(r, 1500));
    const t = await api('/api/vsf/tarefa/' + id);
    logar(`[${t.nome}] ${t.estado}\n${t.log}`);
    if (t.estado !== 'rodando') { carregar(); return t; }
  }
}
async function carregar() {
  try {
    const dados = await api('/api/vsf/roteiros');
    const alvo = $('roteiros');
    alvo.innerHTML = '';
    for (const r of dados.roteiros) {
      const div = document.createElement('div');
      div.className = 'card';
      const takes = r.takes.map(t => `
        <div class="take ${t.status}">
          ${t.existe ? `<video controls preload="metadata"
              src="/api/vsf/midia/${r.slug}/${t.arquivo}"></video>` : '<div class="fraco">arquivo ausente</div>'}
          <div class="fraco">${t.arquivo} · ${t.status}${t.motivos.length ? ' · ' + t.motivos.join('; ') : ''}</div>
          <div class="linha" style="margin-top:6px">
            <button onclick="acao('aprovar',{slug:'${r.slug}',take:'${t.arquivo}'})">Aprovar</button>
            <button class="morto" onclick="rejeitar('${r.slug}','${t.arquivo}')">Regenerar</button>
          </div>
        </div>`).join('') || '<div class="fraco">nenhum take ainda</div>';
      div.innerHTML = `
        <div class="linha">
          <strong>${r.slug}</strong>
          <span class="pill">semana ${r.semana}</span>
          <span class="pill">${r.modelo} · ${r.duracao}s</span>
          <span class="pill">${r.custo_por_take} créditos/take</span>
          ${r.aprovado ? '<span class="pill" style="color:var(--ok)">aprovado: ' + r.aprovado + '</span>' : ''}
          ${r.editado ? '<span class="pill" style="color:var(--ok)">editado</span>' : ''}
        </div>
        <div class="fraco" style="margin-top:6px">gancho: “${r.gancho}” · legenda: ${r.legenda || '—'}</div>
        <div class="linha" style="margin-top:10px">
          <button onclick="acao('gerar',{slug:'${r.slug}',quantidade:1})">Gerar 1 take</button>
          <button class="sec" onclick="acao('gerar',{slug:'${r.slug}',quantidade:2})">Gerar 2</button>
          <button class="sec" onclick="mostrarCusto('${r.slug}')">Simular (sem gastar)</button>
          <button class="sec" onclick="acao('editar',{slug:'${r.slug}'})">Editar aprovado</button>
          <button class="sec" onclick="acao('publicar',{slug:'${r.slug}',privacidade:'private'})">Publicar (privado)</button>
        </div>
        <div class="takes">${takes}</div>`;
      alvo.appendChild(div);
    }
    if (!dados.roteiros.length) alvo.innerHTML = '<div class="card fraco">Nenhum roteiro ainda. Crie um acima.</div>';
  } catch (e) { logar('erro: ' + e.message); }
}
async function acao(rota, corpo) {
  try { const r = await post('/api/vsf/' + rota, corpo);
        if (r.id) acompanhar(r.id); else { logar(JSON.stringify(r)); carregar(); } }
  catch (e) { logar('erro: ' + e.message); }
}
function rejeitar(slug, take) {
  const motivo = prompt('Motivo da regeração (vai para o histórico do take):') || '';
  acao('rejeitar', {slug, take, motivo, regerar: true});
}
async function mostrarCusto(slug) {
  const r = await post('/api/vsf/gerar', {slug, dry_run: true});
  if (r.id) acompanhar(r.id);
}
async function salvarRoteiro() {
  await acao('roteiro', {
    slug: $('f-slug').value.trim(), semana: +$('f-semana').value,
    gancho: $('f-gancho').value, prompt: $('f-prompt').value, titulo: $('f-titulo').value,
    modelo: $('f-modelo').value, duracao: +$('f-duracao').value,
    preset: $('f-preset').value, personagem: $('f-personagem').value || null});
}
(async () => {
  try {
    const c = await api('/api/vsf/creditos');
    $('saldo').textContent = c.ok ? 'créditos: ' + JSON.stringify(c.creditos).slice(0, 90)
                                 : 'créditos: sessão expirada';
  } catch (e) {}
  try { const m = await api('/api/vsf/motor');
        $('motor').textContent = 'motor: ' + (m.texto_ok ? 'OK' : 'SEM TEXTO'); } catch (e) {}
  carregar();
})();
</script>
</body></html>
"""
