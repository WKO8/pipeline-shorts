# Revisão de qualidade — 17/09/2026

## Causas encontradas

- O tracker antigo presumiu a pessoa da direita como falante sem usar áudio.
  O renderer enviava um dicionário de janelas a uma função que esperava tuplas;
  isso podia acionar o fallback de crop central. Rostos centrais eram ignorados
  na seleção entre esquerda/direita. Detecção de pessoa podia substituir
  detecção facial e tratar objetos/corpos como rostos.
- A implementação WhisperX não era chamada pelo caminho normal de transcrição.
- O preset sobrescrevia preferências com fonte 120 e uma palavra por legenda;
  a tag `an5` também ignorava o posicionamento inferior do estilo.
- `aselect` descartava blocos de áudio completos. Vídeo e legendas usavam outra
  base temporal. O tempo de entrada do FFmpeg ainda era limitado à duração
  reduzida, perdendo o final do trecho quando havia remoção de silêncio.
- Tracking reduzido a poucos pontos podia interpolar entre duas pessoas durante
  uma troca de câmera. Janelas sem detecção abertas no final não eram fechadas.
- Blocos de curadoria de 20 minutos eram truncados para 14 mil caracteres.
  Além de omitir conteúdo, o prompt repetia instruções e exigia uma cota de cortes.
- O serviço mantinha o objeto Whisper carregado antes da curadoria: limpar o
  cache CUDA não descarrega pesos ainda referenciados.
- O blur do fundo processava a resolução final com muitas passagens por frame.

## Implementação

YuNet em CPU detecta rostos e olhos. LR-ASD infere atividade audiovisual em 25 fps,
com áudio de 16 kHz, janelas de 4 segundos e sobreposição de 2 segundos. Esse
processamento ocorre só nos cortes selecionados, depois de liberar ASR/LLM.
O código de inferência é vendorizado com licença MIT e revisão registrada.

O tracker associa rostos por IoU dentro de cada plano, reinicia a associação nas
mudanças de cena, exige diferença de score para escolher entre rostos e usa plano
aberto quando não há evidência suficiente. Flashes de foco muito curtos são
suprimidos. Planos de um único rosto seguem a edição original; isso não prova que
essa pessoa esteja falando (pode ser uma reação editada no original).

Os cortes de silêncio são opcionais. Vídeo é normalizado a uma grade de frames,
áudio é aparado com `atrim`, e os intervalos são concatenados. As mesmas janelas
remapeiam legendas e tracking. O render lê a duração original inteira.

## Escolha de modelos

| Opção | Decisão e motivo |
|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Mantido com large-v3-turbo em INT8/FP16; reutiliza infraestrutura e pesos existentes. Não é alegação de melhor WER em qualquer podcast. |
| [WhisperX](https://github.com/m-bain/whisperX) | Alinhamento forçado de português, agora realmente executado após descarregar ASR. |
| [LR-ASD](https://github.com/Junhua-Liao/LR-ASD) | Rede audiovisual leve publicada em IJCV 2025; evita inferir falante apenas por tamanho/posição do rosto. Pesos TalkSet da revisão fixada. |
| [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | Detector facial leve do OpenCV, executado em CPU para poupar VRAM. |
| [Qwen 3.5 4B](https://ollama.com/library/qwen3.5:4b) | Curadoria local gratuita; pacote de cerca de 3,4 GB. Contexto limitado a 8192 tokens no pipeline, não ao máximo anunciado do modelo. |
| [Qwen3-ASR / ForcedAligner](https://github.com/QwenLM/Qwen3-ASR) | Alternativa recente com suporte a português. Não integrada: seria necessário benchmark de transcrição e sincronismo contra o material real antes de substituir o caminho validado. |
| [C³ASD](https://github.com/jisoo-o/C3ASD) | Pesquisa de 2026 consultada; não instalada nem medida. Mais recente não implica melhor relação qualidade/tempo neste hardware. |

São fontes primárias consultadas nesta revisão. Não há garantia de superioridade
universal nem benchmark comparativo completo com todos esses modelos.

## Testes e reprodução

Os testes de regressão cobrem troca de falante sem viés de lado, incerteza,
reinício de cena, supressão de flashes, estilo e pausas de legenda, retenção de
cortes no tracking, remapeamento de legendas vazias, cobertura integral de
transcrições longas e liberação do ASR antes do alinhamento.

Um teste real de FFmpeg aplica 20 cortes a uma fonte de 29,97 fps e verifica 60
frames de saída e 32.000 amostras de áudio a 16 kHz (2 segundos). Isso verifica
sincronia de edição; não mede erro de cada palavra do ASR.

Amostra local: `davy-jones.mp4`, 222,82–252,82 segundos. O relatório e a prévia
estão em `data/quality-review-final/`. Os arquivos anteriores foram preservados.
O benchmark curto não extrapola desempenho de um podcast de três horas nem mede
objetivamente a taxa de acerto dos falantes. Para essa medição, é necessário
anotar manualmente quem fala em uma seleção de trechos, incluindo sobreposições
e oclusões, e comparar os resultados com esses rótulos.

O `.env` local foi ajustado apenas nas opções de qualidade/localidade. Credenciais
não foram copiadas para documentação ou testes. Os diffs do repositório já tinham
mudanças do usuário antes desta revisão; nem tudo no diff foi introduzido aqui.

## Resultado da validação local

- RTX 3050 de 8 GB confirmada pelo driver; ASR, alinhamento e LR-ASD executados
  em CUDA, e exportação por NVENC, sem erro de memória nos trechos testados.
- **23 testes passaram**, incluindo o caso real em que WhisperX divide um
  segmento original em várias frases.
- Prévia de 30 s: 1080×1920, 30 fps, vídeo e áudio com 30,000 s. Execução com
  pesos já disponíveis: 70,91 s. Esse tempo inclui preparação local, ASR,
  alinhamento, tracking, debug e render; não inclui curadoria nem download YouTube.
- Curadoria local de 180–540 s da transcrição original: 19,89 s, dois cortes
  selecionados, com limite de 90 s. Tempos em `curation.json` são relativos ao
  início dessa janela (some 180 s para localizar no podcast).
- Um corte escolhido foi exportado em `data/quality-curated/quality_clip_01.mp4`.
  Intervalo original: 244,88–282,16 s. Alinhamento final: **19/19 segmentos**.
  Vídeo: 37,300 s; áudio: 37,280 s; diferença de 20 ms, inferior a um frame.
  A primeira exportação revelou a divisão de segmentos do WhisperX; ela foi
  corrigida e a amostra foi realinhada e reexportada. O relatório separa o tempo
  inicial do tempo dessa correção, para não apresentá-lo como benchmark único.

A curadoria do modelo local ainda exige revisão editorial: títulos e
justificativas podem extrapolar o conteúdo. Os testes verificam limites,
integridade e comportamento do pipeline, não garantem que os cortes viralizem.
Para refazer o enquadramento de cortes antigos, use `--retrack`; legendas antigas
exigem nova transcrição/alinhamento para incorporar a melhoria de tempos.
