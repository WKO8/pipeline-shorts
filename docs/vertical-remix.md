# Vídeos Verticais

A aba **Vídeos Verticais** da UI existente processa Shorts, TikTok e Reels completos.
Inicie normalmente com `./run_ui.sh`. Salve o estilo em **Estilo & Design**, abra a
nova aba, informe a URL base, escolha o complementar/layout e clique em **Gerar vídeo vertical**.
O resultado aparece no player com download de MP4, ASS/SRT quando gerados e comando FFmpeg.
A mesma console, exclusão mútua entre execuções e ação Cancelar são reutilizadas.

## Pontos de integração

- `ui/index.html`: painel `panel-vertical` e navegação.
- `ui/vertical.js`: formulário, upload, resultados e evento `pipeline-done` emitido por `ui/app.js`.
- `ui/vertical_routes.py`: blueprint registrado por `ui/app.py` com `register_vertical(app, backend)`.
- `src/vertical.py`: ingestão, detecção/cortes, composição e orquestração independente.
- `src/captions.py`: argumento opcional `placement` em `BrainRotCaptionBuilder.build`.
  Chamadas existentes continuam com a aparência anterior.

Cada execução usa `data/clips/vertical/run-*/`, sem sobrescrever os originais.
A pasta contém `request.json`, `options.json`, `timeline.json`, intermediários,
`vertical.mp4` e os comandos/filtros exatos de cada render (`*.command.txt`, `*.filter.txt`).
O processo CLI usa o `.env` atual. Não passa pelo curador de podcasts ou pelo reenquadramento de rostos.

## Ingestão

`download_social(url, directory, config)` aceita hosts do YouTube, TikTok e Instagram,
incluindo links curtos desses domínios. Usa `bestvideo*+bestaudio/best` sem limitar
resolução e mantém o enquadramento original no download. Streams separados são
remuxados em MKV para preservar codecs; a saída final é MP4 H.264/AAC.
O caminho real vem de `--print-to-file after_move:filepath`, sem adivinhar extensões.

Há uma segunda tentativa com User-Agent e Referer da plataforma. Reutiliza
`YTDLP_COOKIES_FROM_BROWSER` / `YTDLP_COOKIES_FILE` e, no YouTube, as configurações
de runtime/extractor existentes. Conteúdo privado, bloqueios e cookies expirados
continuam sujeitos às exigências da plataforma; headers não garantem acesso.
Não aceita playlists como lote nem transmissões ao vivo.

Complementar: arquivos em `assets/vertical/`, upload de até 2 GiB ou URL dessas
mesmas plataformas. Sem complementar, aplica tela cheia. Em tela cheia o
complementar e seu áudio são ignorados. Uploads permanecem em `data/vertical_uploads/`.
Não há download de presets de terceiros incluído.

## Cortes e sincronia

1. Normaliza a base para CFR 30/60 e áudio PCM estéreo a 48 kHz em MKV.
2. Detecta pausas com `silencedetect=noise=-30dB:d=0.4`.
3. Preserva 50 ms nas bordas da fala e arredonda intervalos para a grade de frames.
4. Usa `trim`/`atrim` nos mesmos intervalos. Concatena o vídeo e une o áudio com
   `acrossfade=d=0.005:o=0:c1=tri:c2=tri`.
5. Extrai o WAV da base **já editada** e somente então chama o Whisper existente.

O parâmetro `o=0` aplica fade-out/fade-in de 5 ms sem sobrepor os trechos no tempo.
Isso evita retirar 5 ms do áudio por junção e acumular desvio em relação ao vídeo.
A duração resultante permanece a soma dos intervalos de `timeline.json`. Consulte a
[documentação oficial de acrossfade](https://ffmpeg.org/ffmpeg-filters.html#acrossfade).
Não há silêncio detectado: preserva a timeline. Todo o áudio é silencioso: preserva
original e avisa no log. Sem faixa de áudio: sintetiza silêncio e pula transcrição.
A detecção é por amplitude; música contínua pode impedir a detecção de pausas na fala.

## Composição e comandos exatos

`composition_filter(options, duration, secondary, secondary_audio, ass)` produz o
grafo completo; `ffmpeg_render` registra a invocação exata com `shlex.join`.
O complementar recebe `-stream_loop -1` antes de seu `-i`; `trim` e `-t` limitam a
duração à base editada. `fps` iguala os dois streams, `setsar` normaliza pixels e
`scale` + `crop` preenchem os quadros preservando proporções, inclusive fontes anamórficas.

Exemplo executável (base preparada em `edited.mkv`, complementar em `gameplay.mp4`,
10 segundos, sem legendas, complementar mudo):

```bash
ffmpeg -y -i edited.mkv -stream_loop -1 -i gameplay.mp4 \
  -filter_complex "[0:v]fps=30,scale=iw*sar:ih,setsar=1,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[b];[1:v]setpts=PTS-STARTPTS,fps=30,scale=iw*sar:ih,setsar=1,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1[e];[b][e]vstack=inputs=2:shortest=1[v]" \
  -map '[v]' -map 0:a -t 10 -r 30 -c:v libx264 -crf 18 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -movflags +faststart vertical.mp4
```

Os demais layouts gerados pelo módulo usam:

| Layout | Geometria / operação |
| --- | --- |
| `split_vertical_top_bottom` | Ambos 1080×960, `[base][extra]vstack=inputs=2:shortest=1` |
| `split_side_by_side` | Ambos 540×1920, `[base][extra]hstack=inputs=2:shortest=1` |
| `full_screen` | Base 1080×1920 |
| `pip_overlay` | Base 1080×1920, complementar 324×576, `overlay=x=54:y=154:shortest=1` |

`invert=True` troca `[base][extra]` por `[extra][base]` nos dois splits.
Áudio complementar só é usado se existir, se o volume for maior que zero e se o
layout exibir o complementar. Usa `volume`, `amix=duration=first:normalize=0` e
limiter com compensação de latência; o padrão mudo mantém o nível da base.

Para obter os quatro comandos exatos com seus próprios arquivos:

```python
from pathlib import Path
from src.vertical import LAYOUTS, VerticalOptions, composition_filter, ffmpeg_render

work = Path('/caminho/absoluto/da/execucao')
for layout in LAYOUTS:
    options = VerticalOptions(layout=layout, captions=False)
    inputs = ['-i', str(work / 'edited.mkv')]
    secondary = layout != 'full_screen'
    if secondary:
        inputs += ['-stream_loop', '-1', '-i', str(work / 'gameplay.mp4')]
    graph = composition_filter(options, 10, secondary, False)
    ffmpeg_render(inputs, graph, work / f'{layout}.mp4', 10, 30)
```

## Legendas reutilizadas

```python
from src.captions import BrainRotCaptionBuilder, KineticCaptionStyle, CaptionPlacement
from src.transcriber import WhisperTranscriber

# audio_editado.wav foi extraído DEPOIS dos cortes.
transcriber = WhisperTranscriber(config)
try:
    transcript = transcriber.transcribe('audio_editado.wav')
finally:
    transcriber.unload_model()
style = KineticCaptionStyle.from_config(config)
style.fps = 30
BrainRotCaptionBuilder(style).build(
    transcript, 'captions.ass', 0, duracao_editada,
    placement=CaptionPlacement(y_percent=65, safe_zones=True),
)
```

O pipeline força `whisper_word_timestamps=True` em uma cópia da configuração.
Fontes, cores, bordas, animações e agrupamento vêm integralmente do estilo existente.
`y_percent` é o centro da legenda, medido do topo. Com safe zones, a área útil é
x=54..918 e y=154..1536. A posição é limitada considerando fonte/animação; margens
ASS e `\\clip` impedem invasão da área reservada. Fontes/frases excepcionalmente
grandes podem ser recortadas: ajuste tamanho/quantidade de palavras em Estilo & Design.
Essas margens são conservadoras, não uma garantia sobre todas as versões das interfaces sociais.
SRT exporta os mesmos tempos, sem estilo ou posicionamento. Desligar legendas pula
Whisper e a gravação ASS/SRT; texto embutido no vídeo de origem é preservado.

## Verificação

```bash
.venv/bin/python -m pytest tests/test_vertical.py tests/test_alignment.py tests/test_workflow_improvements.py tests/test_quality.py -q
```

Os testes usam mídia sintética e FFmpeg real para layouts, FPS, duração, loop,
cortes e contagem de amostras. Downloads de redes sociais são simulados nos testes;
autenticação e disponibilidade de links reais dependem do ambiente/plataforma.
