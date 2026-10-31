# Implementação incremental — 18/09/2026

Esta entrega prioriza o fluxo local da RTX 3050 de 8 GB. Não instala modelos,
não habilita provedores pagos e não altera o `.env` com credenciais.

## Fluxo padrão

1. Transcrição completa com faster-whisper e tempos originais por palavra.
2. Curadoria local em janelas, com IDs de segmentos e evidência do gancho/conclusão.
3. Segunda avaliação editorial local de até 12 candidatos distintos. Notas
   separadas para abertura, compreensão isolada e conclusão; a média substitui
   a nota inicial. Não há bônus por números ou palavras chamativas.
4. Alinhamento somente dos segmentos dos cortes selecionados. Segmentos comuns
   a vários cortes são processados uma vez. Vizinhos mantêm suas fronteiras para
   limitar as margens de recuperação. O áudio ainda é decodificado integralmente;
   a redução principal é de inferência do alinhador, não da RAM de decodificação.
5. Limites finais são ampliados quando atravessam palavras. Se a ampliação viola
   a duração permitida, o candidato é rejeitado; a conclusão não é aparada.
6. Tracking, render e página local de revisão com alertas.

A CLI utilizada pela UI e `ShortsPipelineService` usam esse fluxo. Cortes manuais
já processam intervalos curtos e continuam alinhando seu intervalo completo.
`--subtitles-only` alinha a transcrição inteira.

## Checkpoints

`data/cache/stages-v1/` contém ASR, alinhamento, respostas do editor local e
tracking. Chaves incluem conteúdo da fonte, parâmetros relevantes e versões de
bibliotecas onde aplicável. O cache de tracking também verifica os pesos.
Não são serializados credenciais, cabeçalhos HTTP ou o ambiente do processo.
Os checkpoints de texto contêm a transcrição: são arquivos locais do projeto.

O arquivo de áudio extraído de um vídeo local inclui o hash do vídeo no nome,
evita reutilizar áudio de outro conteúdo com o mesmo nome. O checkpoint de ASR
é salvo antes do alinhamento e pode ser reutilizado após interrupções. Uma
transcrição feita no retry degradado de memória não substitui o checkpoint da
configuração original.

Checkpoints inválidos/incompletos são ignorados e preservados. Há checksum do
payload, e cada gravação usa um arquivo novo. `--no-cache` recalcula sem apagar.
`--force-curate` ignora tanto a seleção salva quanto as respostas locais salvas,
preservando o reaproveitamento das demais etapas.

O alinhamento parcial bem-sucedido também é reutilizável. Para tentar novamente
os trechos que permaneceram com tempos originais, use `--no-cache` ou altere a
configuração de alinhamento. Use igualmente `--no-cache` depois de atualizar pesos
locais de ASR/alinhamento ou trocar o conteúdo de uma tag Ollama sem mudar seu nome.
Não há cache do MP4 final: uma nova exportação gera uma nova execução, mas pode
reaproveitar transcrição, curadoria, alinhamento e tracking.

## Curadoria e legendas

O schema local pede `start_segment_id`, `end_segment_id`, `hook_segment_id` e
`payoff_segment_id`. Referências inexistentes ou fora de ordem são rejeitadas;
segundos inventados não substituem referências inválidas. A justificativa agrega
as falas originais referenciadas. A compatibilidade com JSONs antigos e provedores
externos que devolvem segundos foi mantida.

A revisão editorial adicional é limitada, local e sequencial. Se o modelo falhar,
os candidatos são mantidos com a marca `review-incomplete`. Uma rejeição editorial
válida pode reduzir a quantidade de cortes. A nota continua sendo uma avaliação
não calibrada; não prevê viralidade. A revisão acrescenta inferência na primeira
execução, então não se promete que todo vídeo termine mais rápido.

Nas legendas, o agrupamento evita deixar artigos, preposições e a negação `não`
isolados no fim de um bloco quando é possível movê-los junto à palavra seguinte,
respeitando limites e pausas. Não implementa ainda análise sintática completa
nem medição tipográfica da largura real.

## Preservação e revisão

Novas exportações ficam em `data/clips/profile_3/<slug>/run-<data>-<id>/`.
O fluxo padrão gera `review.html`, `quality-review.json`, `selected-clips.json` e
uma cópia da transcrição usada no render. `--dry-run` gera revisão de candidatos
em `data/reviews/<slug>/run-*/`, sem exportar vídeo.

A página é estática, local, com texto escapado, player e transcrição. Não edita
os cortes por si só: serve para inspeção. A edição existente de legendas e o
rerender continuam disponíveis. Os metadados agora distinguem execuções pelo
caminho do clipe; um nome repetido não seleciona a câmera de outra execução.

Limpeza automática foi desativada, incluindo diretórios temporários de tracking,
conversão e upscaling. As funções de limpeza da aplicação preservam arquivos mesmo
com uma configuração antiga `KEEP_INTERMEDIATE_FILES=false`. JSONs substituídos,
legendas regeneradas e vídeos substituídos no rerender preservam revisões em
`.history/`. Não foi criada nenhuma rotina de remoção; o armazenamento crescerá.

## Provedores externos

`CURATOR_PROVIDER=ollama` continua sendo o padrão. Uma chamada paga exige:

- `API_PRICED_MODEL`: o nome exato do modelo autorizado pela tarifa configurada.
- `API_INPUT_USD_PER_MILLION` e `API_OUTPUT_USD_PER_MILLION`: preços verificados.
- `API_BUDGET_USD`: limite de reserva por instância de curadoria; padrão US$ 0,05.
- `API_MAX_OUTPUT_TOKENS`: limite de saída; padrão 2048.

Preços zero, orçamento zero ou modelo diferente bloqueiam a chamada antes do
envio. A estimativa usa os bytes UTF-8 do prompt com margem como limite
conservador de tokens de entrada, além da saída máxima. As reservas são
cumulativas e não são devolvidas após timeout, pois a requisição pode ter sido
faturada. Fallbacks compartilham orçamento; uma tarifa de um modelo não é aplicada
a outro. No provedor `auto`, indisponibilidade leva ao modelo local. No provedor
pago explicitamente selecionado, a falha é reportada.

Consumo informado pelos provedores é registrado junto às reservas nos metadados
da curadoria. Esse controle depende das tarifas corretas e das regras de cobrança
do provedor; não é um limite financeiro imposto na conta externa. Impostos,
câmbio e energia não entram no cálculo.

## Validação e próximos experimentos

Os testes cobrem repetição sem nova transcrição, invalidação por conteúdo/modelo,
cache corrompido preservado, alinhamento seletivo sem duplicação, IDs/evidências,
veto editorial e fallback, limites de palavras, reservas de API, preservação de
saídas, agrupamento de legendas e integração da CLI e do serviço.

As chamadas de modelos são simuladas nesses testes, sem pagamentos ou downloads.
A suíte também contém um teste real de FFmpeg com cortes de áudio/vídeo. Ainda
não há benchmark comparativo de GPU ou medição humana da qualidade editorial
para estas mudanças.

Não foram incluídos nesta entrega: novo ASR/alinhador, embeddings semânticos,
curadoria multimodal, áudio em duas passagens, novo algoritmo de câmera ou editor
visual completo. Esses itens exigem comparação controlada com o material real;
não são necessários para usar o fluxo incremental implementado.

## Resultado da validação

- 54 testes automatizados, incluindo os testes de integração da CLI e do serviço,
  cache do editor local, invalidação de tracking e seleção exata dos metadados de
  rerender entre duas gerações com o mesmo nome de clipe.
- Prévia sintética exportada pelo renderer real na RTX 3050 com NVENC, legendas
  ASS, áudio normalizado e arquivos modulares. Resultado: 1080×1920, vídeo e áudio
  com 5,000 s. Frame inspecionado visualmente: legenda legível e dentro da tela.
- Nenhum podcast, credencial ou serviço pago foi usado na prévia. Ela pode ser
  reproduzida com `.venv/bin/python scripts/validate_incremental.py`; cada execução
  gera uma pasta nova dentro de `data/validation/incremental/`.
- Esse teste não mede qualidade editorial nem velocidade de um vídeo longo.
