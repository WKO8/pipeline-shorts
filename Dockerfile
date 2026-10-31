# ==============================================================================
# Pipeline Shorts — Container de Produção e Execução Local
# ==============================================================================
FROM python:3.11-slim

# Evita geração de bytecode .pyc e força flush imediato de stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    HOST=0.0.0.0 \
    PORT=7860 \
    PYTHONPATH=/app

WORKDIR /app

# Instala pacotes essenciais do sistema:
# build-essential (compilação C/C++), ffmpeg (vídeo), libgl1 / libglib2.0-0 (OpenCV), git, curl (healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copia binário oficial do uv para resolução ultrarrápida de dependências
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copia requirements e instala dependências Python via uv
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

# Copia código da aplicação
COPY src/ /app/src/
COPY ui/ /app/ui/
COPY scripts/ /app/scripts/
COPY main.py sitecustomize.py /app/

# Cria diretórios para dados e arquivos temporários
RUN mkdir -p /app/data /app/tmp

# Expõe a porta padrão da UI / API
EXPOSE 7860

# Verificação de integridade do container
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7860/api/health || exit 1

# Comando de inicialização escutando em 0.0.0.0:7860
CMD ["python", "ui/app.py"]
