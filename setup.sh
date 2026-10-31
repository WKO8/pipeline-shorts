#!/usr/bin/env sh
set -eu

# --------------------------------------------
# setup.sh
# Local setup helper for pipeline-shorts-build
# --------------------------------------------

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
REQ_FILE="$PROJECT_DIR/requirements.txt"
ENV_EXAMPLE="$PROJECT_DIR/.env.example"
ENV_FILE="$PROJECT_DIR/.env"

echo "==> Pipeline Shorts Build | Setup"

# ---- helpers ----
has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

warn() {
  printf '%s\n' "WARN: $1"
}

info() {
  printf '%s\n' "INFO: $1"
}

fail() {
  printf '%s\n' "ERROR: $1" >&2
  exit 1
}

# ---- basic checks ----
has_cmd python3 || fail "python3 não encontrado. Instale Python 3.10+."
has_cmd ffmpeg || warn "ffmpeg não encontrado. O pipeline precisa dele para render/corte."
has_cmd yt-dlp || warn "yt-dlp não encontrado globalmente. Será instalado no venv via pip."
has_cmd ollama || warn "ollama não encontrado. A curadoria por LLM local não funcionará sem ele."

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
info "Python detectado: $PY_VER"

# ---- create virtualenv ----
if [ ! -d "$VENV_DIR" ]; then
  info "Criando virtualenv em $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  info "Virtualenv já existe: $VENV_DIR"
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

info "Atualizando pip/setuptools/wheel"
python -m pip install --upgrade pip setuptools wheel

# ---- install python deps ----
if [ -f "$REQ_FILE" ]; then
  info "Instalando dependências de $REQ_FILE"
  python -m pip install -r "$REQ_FILE"
else
  fail "Arquivo requirements.txt não encontrado em $REQ_FILE"
fi

# ---- ensure .env ----
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$ENV_EXAMPLE" ]; then
    info "Criando .env a partir de .env.example"
    cp "$ENV_EXAMPLE" "$ENV_FILE"
  else
    warn ".env.example não encontrado. Criando .env vazio."
    : > "$ENV_FILE"
  fi
else
  info ".env já existe, mantendo arquivo atual."
fi

# ---- optional ollama checks ----
if has_cmd ollama; then
  info "Verificando Ollama local..."
  if ollama list >/dev/null 2>&1; then
    if ! ollama list | grep -qi 'llama3'; then
      warn "Modelo 'llama3' não encontrado no Ollama."
      warn "Para instalar: ollama pull llama3"
    else
      info "Modelo llama3 detectado no Ollama."
    fi
  else
    warn "Ollama está instalado, mas parece não estar em execução."
    warn "Inicie e depois rode: ollama pull llama3"
  fi
fi

# ---- quick runtime guidance ----
cat <<EOF

✅ Setup concluído.

Próximos passos:
1) Ativar ambiente:
   source "$VENV_DIR/bin/activate"

2) (Opcional) Configurar variáveis:
   nano "$ENV_FILE"

3) Garantir Ollama com modelo:
   ollama serve
   ollama pull llama3

4) Rodar pipeline (exemplo):
   python main.py --url "https://www.youtube.com/watch?v=SEU_VIDEO"

Observações:
- ffmpeg deve estar instalado no sistema.
- O pipeline usa Whisper local (CPU/GPU conforme configuração).
EOF
