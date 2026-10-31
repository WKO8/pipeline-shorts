"""Upload no YouTube (Data API v3, `videos.insert`) usando só a stdlib.

Reaproveita a credencial OAuth que já existe no projeto `vosemfiltro-analytics`
(escopo `youtube.force-ssl`, que já cobre upload): nenhum arquivo de segredo é
copiado — apenas apontado por variável de ambiente.

    VSF_YT_CLIENT_SECRET  client_secret.json do app OAuth
    VSF_YT_TOKEN          token.json (refresh token do canal)

Cota: uploads têm bucket próprio (100/dia por padrão), então não competem com as
10.000 unidades usadas pelos outros endpoints.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = ("https://www.googleapis.com/upload/youtube/v3/videos"
              "?uploadType=resumable&part=snippet,status")


class PublicacaoFalhou(RuntimeError):
    """Falha ao falar com a API do YouTube."""


class CredencialAusente(PublicacaoFalhou):
    """Faltam client_secret.json / token.json para publicar."""


def caminho_credencial(nome: str) -> Path:
    """Caminho da credencial, configurável por ambiente."""
    valor = os.environ.get(nome)
    if not valor:
        raise CredencialAusente(
            f"defina {nome} — ex.: export {nome}="
            "/caminho/para/credenciais/"
            + ("client_secret.json" if "CLIENT" in nome else "token.json"))
    caminho = Path(valor).expanduser()
    if not caminho.exists():
        raise CredencialAusente(f"{nome} aponta para {caminho}, que não existe")
    return caminho


def _post_json(url: str, dados: dict) -> dict:
    corpo = urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as resposta:
            return json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        raise PublicacaoFalhou(f"{url} devolveu {erro.code}: "
                               f"{erro.read().decode(errors='replace')[:300]}") from erro


def access_token(client_secret: Path | None = None, token: Path | None = None) -> str:
    """Troca o refresh token por um access token novo."""
    client_secret = client_secret or caminho_credencial("VSF_YT_CLIENT_SECRET")
    token = token or caminho_credencial("VSF_YT_TOKEN")
    dados_cliente = json.loads(Path(client_secret).read_text(encoding="utf-8"))
    bloco = dados_cliente.get("installed") or dados_cliente.get("web") or {}
    guardado = json.loads(Path(token).read_text(encoding="utf-8"))
    refresh = guardado.get("refresh_token")
    if not refresh:
        raise CredencialAusente(
            f"{token} não tem refresh_token (token só de acesso?). Rode o fluxo de "
            "autorização do projeto de analytics novamente.")
    resposta = _post_json(TOKEN_URL, {
        "client_id": bloco["client_id"], "client_secret": bloco["client_secret"],
        "refresh_token": refresh, "grant_type": "refresh_token"})
    acesso = resposta.get("access_token")
    if not acesso:
        raise PublicacaoFalhou(f"troca de token recusada: {resposta}")
    return str(acesso)


@dataclass
class MetadadosVideo:
    titulo: str
    descricao: str = ""
    tags: list[str] | None = None
    categoria: str = "22"              # 22 = People & Blogs
    privacidade: str = "private"        # private | public | unlisted
    publicar_em: str | None = None      # ISO-8601 UTC (agendamento)
    feito_para_criancas: bool = False

    def corpo(self) -> dict:
        status: dict = {"privacyStatus": self.privacidade,
                        "selfDeclaredMadeForKids": self.feito_para_criancas}
        if self.publicar_em:
            status["privacyStatus"] = "private"
            status["publishAt"] = self.publicar_em
        return {
            "snippet": {"title": self.titulo[:100], "description": self.descricao[:5000],
                        "tags": self.tags or [], "categoryId": self.categoria},
            "status": status,
        }


def _cabecalhos(token: str, tamanho: int, corpo: dict) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
            "X-Upload-Content-Length": str(tamanho), "X-Upload-Content-Type": "video/mp4",
            "Content-Length": str(len(json.dumps(corpo).encode()))}


def enviar(video: Path, meta: MetadadosVideo, *, token: str | None = None,
           client_secret: Path | None = None, arquivo_token: Path | None = None) -> dict:
    """Publica (ou agenda) o vídeo e devolve o recurso criado pela API."""
    video = Path(video)
    if not video.exists():
        raise PublicacaoFalhou(f"vídeo não encontrado: {video}")
    token = token or access_token(client_secret, arquivo_token)
    corpo = meta.corpo()

    # 1) abre a sessão resumable
    requisicao = urllib.request.Request(UPLOAD_URL, data=json.dumps(corpo).encode(),
                                        method="POST")
    for chave, valor in _cabecalhos(token, video.stat().st_size, corpo).items():
        requisicao.add_header(chave, valor)
    try:
        with urllib.request.urlopen(requisicao, timeout=120) as resposta:
            sessao = resposta.headers.get("Location")
    except urllib.error.HTTPError as erro:
        raise PublicacaoFalhou(
            f"abertura da sessão de upload falhou ({erro.code}): "
            f"{erro.read().decode(errors='replace')[:300]}") from erro
    if not sessao:
        raise PublicacaoFalhou("a API não devolveu a URL da sessão de upload")

    # 2) envia os bytes
    envio = urllib.request.Request(sessao, data=video.read_bytes(), method="PUT")
    envio.add_header("Content-Type", "video/mp4")
    try:
        with urllib.request.urlopen(envio, timeout=1800) as resposta:
            return json.loads(resposta.read())
    except urllib.error.HTTPError as erro:
        raise PublicacaoFalhou(
            f"envio do vídeo falhou ({erro.code}): "
            f"{erro.read().decode(errors='replace')[:300]}") from erro


def link(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
