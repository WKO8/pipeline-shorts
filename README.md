# Pipeline Shorts

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Active%20Development-brightgreen.svg)]()
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)]()
[![Docker](https://img.shields.io/badge/Docker-Compose%20Ready-2496ED.svg)]()
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

Gera cortes verticais de podcasts do YouTube ou arquivos locais. O padrão opera como uma **Workstation/Studio local privada de IA**, utilizando modelos locais, sem cobrança de APIs externas e com processamento sequencial otimizado para GPUs a partir de 8 GB (ex.: RTX 3050).

Montagem editorial opcional e experimental: `--narrative-edit` propõe limites e permite remover
digressões; `--semantic-montage` procura retomadas distantes; `--hook-preview`
repete uma passagem chamativa antes da narrativa. `--jev-triage` adiciona a triagem
TypeSafe com orçamento limitado e alternativa local. Pausas: `--remove-silence
--silence-min-seconds 0.9 --silence-padding-seconds 0.18`.
Veja [montagem narrativa e Jev](docs/montagem-narrativa-e-jev.md) para comandos,
custos, funcionamento e limites. Os novos modos preservam o estilo visual atual.
A seleção narrativa com o modelo local ainda requer revisão; comece com `--dry-run`.

---

## ⚡ Execução Rápida via Docker Compose (Recomendado)

O Pipeline Shorts roda como uma **Workstation privada local de IA** em container isolado, com aceleração nativa por hardware e sem necessidade de nuvem, servidores externos ou proxies reversos:

### Pré-requisitos
1. Docker Engine e Docker Compose v2 instalados.
2. [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) instalado para repasse da GPU NVIDIA.
3. Ollama em execução no host local (`ollama serve` e `ollama pull qwen3.5:4b`).

### Inicialização em 1 Clique

```bash
# 1. Garanta o arquivo de variáveis de ambiente
cp -n .env.example .env

# 2. Suba o container com GPU e integração com o Ollama do host
docker compose up -d
```

A interface web estará disponível imediatamente em:
👉 **`http://localhost:7860`**

- **Aceleração por GPU:** A GPU NVIDIA é alocada diretamente ao container via driver de hardware.
- **Comunicação Direta com o Host:** O container acessa o daemon do Ollama do host via `host.docker.internal:11434`.
- **Persistência de Dados:** Pastas `./data`, `./assets` e o arquivo `./.env` são sincronizados com volumes do host.

Comandos úteis:
```bash
# Visualizar logs em tempo real
docker compose logs -f pipeline-shorts

# Verificar status de saúde (healthcheck)
curl http://localhost:7860/api/health

# Parar o serviço
docker compose down
```

---

## Preparação Bare Metal (Python Virtualenv)

Caso prefira executar diretamente no sistema operacional sem Docker:

Use o ambiente virtual do projeto. FFmpeg com `libass` e NVENC e driver NVIDIA
funcional são necessários para exportação acelerada.

```bash
source .venv/bin/activate
uv pip install --python .venv/bin/python -r requirements.txt
python scripts/setup_quality_models.py
ollama pull qwen3.5:4b
```

Em uma instalação nova, copie `.env.example` para `.env`. O primeiro uso do ASR
baixa Whisper large-v3-turbo e o alinhador wav2vec2 de português. Os pesos YuNet
(~230 KB) e LR-ASD (~3,4 MB) são verificados por SHA-256 pelo script acima.
O Ollama deve estar em execução em `localhost:11434`.

A interface pode ser iniciada com:
```bash
./run_ui.sh
```

## Gerar cortes via Linha de Comando (CLI)

```bash
python main.py --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --curator ollama --language pt --max-clips 5 \
  --clip-min-seconds 30 --clip-max-seconds 90
```

O número de cortes é um limite, não uma cota obrigatória. Uma história incompleta
ou sem interesse suficiente pode ser descartada. Scores são avaliações do modelo,
não previsão comprovada de viralidade.

Para conferir um trecho local antes de processar o podcast todo:

```bash
python scripts/quality_preview.py \
  --video data/downloads/podcast.mp4 --start 222.82 --duration 30 \
  --output-dir data/minha-previa
```

A prévia não usa curadoria nem APIs. Exporta o vídeo, transcrição, ASS/SRT,
diagnóstico de tracking e relatório em uma pasta própria. Use uma pasta nova para
preservar uma comparação anterior. O vídeo de diagnóstico não inclui áudio.

## Pipeline de qualidade

1. **ASR:** faster-whisper large-v3-turbo, `int8_float16`, VAD e sem condicionar
   a transcrição ao texto anterior.
2. **Curadoria:** Qwen 3.5 4B via Ollama; janelas limitadas por tempo e texto,
   seleção por IDs e revisão editorial local dos candidatos.
3. **Legendas:** após descarregar ASR/LLM, WhisperX alinha somente os segmentos
   selecionados. O JSON registra `timing_source`; resultados parciais são avisados.
4. **Câmera:** YuNet detecta rostos; LR-ASD cruza áudio e imagem em blocos de até
   quatro segundos. Cortes da câmera original reiniciam a associação de rostos.
   Em caso de dúvida, preserva a cena inteira sobre fundo desfocado.
5. **Render:** NVENC, um clipe por vez; legendas de até quatro palavras, sem
   animação por padrão, sem split automático nem cortes de respiração.

A detecção de falante é probabilística: oclusões, fala simultânea, dublagem,
rostos pequenos e reações podem exigir revisão. IDs de rosto são locais ao plano,
não identidades permanentes nem diarização de pessoas ao longo do podcast.

## Ajustes principais

| Configuração | Padrão | Efeito |
|---|---|---|
| `CURATOR_PROVIDER` | `ollama` | Mantém a curadoria local. |
| `OLLAMA_MODEL` | `qwen3.5:4b` | Modelo local de aproximadamente 3,4 GB. |
| `WHISPER_ALIGNMENT` | `true` | Alinha texto ao áudio para melhorar tempos. |
| `ASD_DEVICE` | `auto` | Usa CUDA quando disponível, CPU caso contrário. |
| `ASD_THRESHOLD` / `ASD_MARGIN` | `0.7` / `0.2` | Exigem evidência e diferença entre os rostos. |
| `ASD_HOLD_SECONDS` | `0.4` | Evita trocar por picos curtos de score. |
| `FORCE_SINGLE_LAYOUT` | `true` | Impede split; permite plano aberto de segurança. |
| `CAPTIONS_POSITION` | `bottom` | Use `center` para centralizar. |
| `CAPTIONS_ANIMATED` | `false` | Animações opcionais. |
| `RENDER_PARALLEL_JOBS` | `1` | Evita concorrência por VRAM. |
| `SILENCE_JUMP_CUTS_ENABLED` | `false` | Preserva pausas naturais. |

`--remove-silence` habilita cortes, com trims de áudio por amostra e uma única
linha do tempo para vídeo/legendas/tracking. `--keep-silence` desabilita.
Zoom, enriquecimento e restauração de rostos são opcionais; não melhoram
necessariamente fidelidade ou enquadramento.

Configurações de estilo da UI/.env são respeitadas; o perfil 3 não as sobrescreve.

## Validação e limites

```bash
python -m pytest tests/test_quality.py -q
python debug_tracking.py --video data/downloads/podcast.mp4 \
  --start 222.82 --end 252.82 --output data/tracking-debug.mp4
```

Veja [diagnóstico, pesquisa e resultados](docs/QUALITY.md). A execução local tem
custo de API zero; eletricidade, armazenamento e internet não estão incluídos.
Provedores pagos continuam opcionais: configure um identificador de modelo válido
no provedor e confira os preços antes de escolhê-los. Chamadas externas exigem tarifas explicitamente configuradas e reserva dentro
do orçamento por execução; veja os limites do controle abaixo.

Diagnóstico do log de alinhamento, ajustes de qualidade e desempenho: [guia de ajustes](docs/qualidade-e-desempenho.md).

## Execução incremental e revisão editorial

O fluxo padrão transcreve uma vez, seleciona cortes por IDs de segmentos, faz uma
segunda avaliação editorial local e alinha somente os segmentos dos cortes finais.
ASR, alinhamento, respostas locais e tracking têm checkpoints em `data/cache/`.
`--subtitles-only` continua alinhando o arquivo completo; `--align-full` solicita
esse comportamento também no fluxo de cortes.

Cada render vai para uma nova pasta `run-*`, com `review.html`,
`quality-review.json`, `selected-clips.json` e a transcrição utilizada. A página
local permite assistir e conferir os alertas; não publica nem envia arquivos.
`--dry-run` gera a revisão dos candidatos sem renderizar. Use `--no-cache` para
recalcular, `--force-curate` para renovar apenas a curadoria e
`--no-editorial-review` para desativar a segunda avaliação local.

Limpeza automática está desativada, inclusive para intermediários. Revisões dos
JSONs substituídos são preservadas em `.history/`; nenhuma opção de recomputação
apaga checkpoints. A configuração local `.env` não foi alterada nesta implementação.

APIs externas exigem `API_PRICED_MODEL` correspondente ao modelo solicitado e
preços de entrada/saída verificados. Há reserva conservadora antes de cada chamada,
com teto `API_BUDGET_USD` (US$ 0,05 por padrão). Preços zerados bloqueiam chamadas;
no provedor `auto`, a execução termina no modelo local caso não haja opção externa
permitida. Veja [escopo, limites e validação](docs/implementacao-incremental.md).

## Remix de vídeos já verticais

A aba **Vídeos Verticais** aceita Shorts, TikTok e Reels, com complementar por
URL, upload ou galeria em `assets/vertical/`. Inclui quatro layouts, jump cuts
opcionais e reutiliza o estilo de legendas salvo, com posição Y e safe zones.
Saída: MP4 1080×1920 em 30/60 FPS, com preview e download na própria aba.

Veja [integração, comandos FFmpeg e testes](docs/vertical-remix.md).

## Produção automática de Shorts

A aba **Produção Automática** (`/vsf-produce`) e o módulo `src/vsf_produce/` integram o
fluxo roteiro → geração via Flow → aprovação de takes → edição → publicação.
A edição usa o motor do Shotcut (`melt`) com os presets visuais configurados
(gancho em Anton, legendas em Montserrat Black, 1080×1920 @ 60 fps) e remove o silêncio
inicial dos clipes gerados automaticamente.

```bash
PY=.venv/bin/python
$PY -m src.vsf_produce.cli novo --slug meu-short --semana 3 --gancho "GANCHO NA TELA" \
  --prompt "prompt enviado ao gerador" --titulo "Título do Vídeo"
$PY -m src.vsf_produce.cli gerar meu-short --dry-run     # comando + estimativa, sem gastar crédito
$PY -m src.vsf_produce.cli gerar meu-short --quantidade 2
$PY -m src.vsf_produce.cli aprovar meu-short take2.mp4
$PY -m src.vsf_produce.cli editar meu-short
$PY -m src.vsf_produce.cli publicar meu-short --privacidade private
```

Os vídeos gerados são armazenados no diretório configurado de edição (por padrão em `~/Videos/PipelineShorts/Edited-Videos/Semana<N>/` ou conforme definido no `.env`).
A geração e edição requerem a ferramenta de CLI autenticada e o Shotcut (`melt`);
detalhes de configuração, créditos e diretrizes de cada plataforma podem ser consultados em
[guia de produção automatizada](docs/vsf-produce.md).

---

## Contribuição

Contribuições são bem-vindas! Consulte o guia em [CONTRIBUTING.md](CONTRIBUTING.md) para detalhes sobre configuração do ambiente local, convenção de Conventional Commits (`feat:`, `fix:`, `docs:`, etc.), execução da suíte de testes (`PYTHONPATH=. .venv/bin/pytest`), testes com Docker Compose e diretrizes da política de segurança.

## Histórico de Versões

As principais notas de versão e evolução do projeto estão documentadas em [CHANGELOG.md](CHANGELOG.md), estruturado de acordo com o padrão *Keep a Changelog* e versionamento semântico.

## Licença e Proteção Comercial

Este projeto é software livre distribuído sob os termos da licença **[GNU Affero General Public License v3 (AGPLv3)](LICENSE)**.

A licença AGPLv3 assegura que qualquer modificação ou incorporação deste software disponibilizada como serviço através de uma rede de computadores (SaaS em nuvem) obrigatoriamente deve fornecer o código-fonte completo correspondente sob a mesma licença, impedindo apropriação indevida e concorrência comercial desleal com o projeto de código aberto.
