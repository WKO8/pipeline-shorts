# Changelog

Todas as alterações notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/),
e este projeto adere ao [Semantic Versioning](https://semver.org/lang/pt-BR/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-30

### Added

#### Containerização e Execução Local com Docker
- Criação do `Dockerfile` de produção local baseado em `python:3.11-slim`, com `ffmpeg`, dependências de OpenCV, aceleração e verificação de saúde (*healthcheck*) em `/api/health`.
- Configuração de `docker-compose.yml` para execução 1-clique no modelo Workstation/Studio local privada de IA na porta `7860`.
- Reserva e repasse de hardware com aceleração por GPU NVIDIA via NVIDIA Container Toolkit (`capabilities: [gpu]`).
- Integração de rede direta com o serviço Ollama em execução no host local via `host.docker.internal:11434`.
- Mapeamento de volumes persistentes para diretórios de trabalho (`./data`), assets (`./assets`) e configurações (`./.env`).

#### ASR e Transcrição Local
- Integração de reconhecimento automático de fala (ASR) via `faster-whisper` com modelo `large-v3-turbo` em precisão `int8_float16`.
- Detecção de atividade de voz (VAD) integrada e supressão de condicionamento forçado para evitar alucinações.
- Cache de checkpoints incrementais em `data/cache/` para persistência de transcrições e reaproveitamento sem reprocessamento.

#### Curadoria Editorial com LLM Local
- Curadoria automatizada de cortes com `qwen3.5:4b` executado localmente via Ollama (porta `11434`), eliminando custos de API.
- Mecanismo de curadoria em dois estágios: segmentação por IDs e segunda avaliação editorial com pontuações de retenção e relevância.
- Modo de execução simulada (`--dry-run`) para inspeção dos candidatos e métricas antes de disparar a renderização.
- Travas de segurança orçamentária (`API_BUDGET_USD`) e verificação tarifária para provedores de LLM opcionais externos.

#### Alinhamento Fonético e Legendas
- Alinhamento de legendas por palavra usando `whisperx` e modelo acústico `wav2vec2` em português.
- Renderização de legendas em formatos ASS e SRT com controle de estilo (posicionamento, cores, bordas, sombras e safe zones).
- Formatação de legendas concisas (1 a 4 palavras por frame), otimizadas para alto engajamento em plataformas verticais.

#### Rastreamento Facial e Enquadramento Inteligente
- Detecção rápida e precisa de rostos via OpenCV YuNet com pesos validados por SHA-256.
- Detecção audiovisual de falante ativo (Active Speaker Detection - ASD) utilizando arquitetura neural LR-ASD.
- Enquadramento dinâmico vertical 9:16 (1080×1920) com plano aberto seguro e fundo desfocado (*blurred background*) para cenas em grupo.

#### Edição, Efeitos e Montagem Narrativa
- Remoção adaptativa de silêncios (`--remove-silence`), mantendo sincronia temporal perfeita entre faixas de áudio, vídeo, legendas e tracking.
- Recursos de montagem narrativa: `--narrative-edit` para poda de digressões, `--semantic-montage` para conexão de tópicos correlatos e `--hook-preview` para inclusão de ganchos iniciais impactantes.
- Módulo de remix de vídeos já verticais (Shorts, TikTok, Reels) com quatro layouts predefinidos e suporte a mídias complementares.
- Módulo de automação de produção (`src/vsf_produce`) e integração com o motor de renderização `melt` (Shotcut) a 60 fps.

#### Interface, Diagnóstico e Testes
- Interface Web local em Flask/HTML5 para pré-visualização, ajuste fino de parâmetros e download de cortes finais.
- Scripts utilitários de diagnóstico: `scripts/quality_preview.py` e `debug_tracking.py` para inspeção visual de enquadramento.
- Relatórios locais de qualidade com `review.html`, `quality-review.json` e logs de auditoria por execução.
- Suíte completa de testes automatizados via `pytest` cobrindo qualidade, alinhamento, cortes narrativos e produção.

#### Licenciamento e Governança
- Licenciamento oficial sob a **GNU Affero General Public License v3 (AGPLv3)**, protegendo o código contra apropriação comercial fechada ou exploração em nuvem (SaaS) sem abertura do código derivado.
- Guia de contribuição da comunidade em `CONTRIBUTING.md`, adotando o padrão Conventional Commits e política de segurança com divulgação responsável.

### Removed
- Remoção da pasta legada `deploy/` e de templates ou configurações de proxies reversos externos (Caddy/Nginx) e scripts de nuvem, consolidando a arquitetura como Workstation/Studio de IA 100% privada e local.
