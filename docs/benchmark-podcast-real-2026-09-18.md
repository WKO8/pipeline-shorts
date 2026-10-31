# Benchmark real — RTX 3050 8 GB, 18/09/2026

O ganho de desempenho foi medido em conteúdo real. A melhoria editorial é parcial:
o teste encontrou uma regressão no revisor, permitiu corrigi-la e expôs limitações
que continuam presentes. Não há medição de retenção, viralidade ou qualidade
generalizável a outros podcasts.

## Material e método

- Vídeo local `data/downloads/davy-jones.mp4`: 1h55min21,54s,
  1920×1080, AV1, 60000/1001 fps, áudio Opus; 1.359.143.554 bytes.
- GPU real: NVIDIA GeForce RTX 3050, 8192 MiB. Whisper large-v3-turbo
  em CUDA/int8_float16; alinhador português wav2vec2 já instalado;
  Qwen 3.5 4B local via Ollama.
- Três janelas predeterminadas: 30–36, 60–66 e 90–96 minutos. São 18 minutos
  de **um episódio**, não três podcasts independentes.
- Nova transcrição dos primeiros 6 minutos da amostra para medir ASR e alinhamento.
  Curadoria usa a transcrição existente, idêntica em todos os braços da comparação,
  isolando o efeito da revisão das diferenças de reconhecimento de fala.
- Candidatos gerados uma vez; revisão desligada, revisão original e revisão
  corrigida recebem os mesmos candidatos. Piso 8,5; máximo três cortes por janela;
  duração de 20 a 180 segundos. Sem baixar o piso para produzir um resultado.
- Comparação entre configurações do código atual, **não** entre uma revisão Git
  antiga e a atual. O projeto já continha alterações anteriores ao benchmark.
- Chamadas pagas: nenhuma. Custo de API: US$ 0. Energia elétrica não medida.
  Configuração criada diretamente, sem carregar `.env` ou credenciais.

## Desempenho

| Etapa | Medição |
|---|---:|
| Extrair 6 min de áudio do vídeo | 0,72 s |
| ASR sem cache, incluindo carregamento | 28,94 s |
| Recuperar ASR do cache, mediana de 3 leituras | 0,013 s |
| Alinhamento completo aquecido, duas repetições | 19,06 / 13,23 s |
| Alinhamento seletivo aquecido, duas repetições | 3,12 / 3,22 s |
| Cache de alinhamento, mediana de 3 leituras | 0,049 s |
| Geração de candidatos dos 18 min, sem revisão | 120,43 s |
| Revisão original dos candidatos, adicional | 75,78 s |
| Reavaliação corrigida, execução separada | 112,58 s |
| Rastreamento audiovisual do corte real de 31,78 s, primeira execução | 54,40 s |
| Cache do rastreamento, mediana de 3 leituras | 0,744 s |
| Exportação NVENC com legendas e arquivos modulares, rastreamento já pronto | 13,35 s |

As medianas aquecidas do alinhamento são **16,14 → 3,17 segundos: redução de
80,3% do tempo dessa etapa**, aproximadamente 5,1×. O seletivo processou 34 dos
176 segmentos, correspondentes a um intervalo fixo de 60 segundos dentro da
amostra de 6 minutos. Esse intervalo é uma sonda de desempenho, não um corte
aprovado pela curadoria. Os segmentos selecionados e seus tempos de palavras
ficaram idênticos aos do alinhamento completo.

O alinhamento completo aceitou 174/176 segmentos, mantendo ASR nos outros dois;
o seletivo aceitou os 34 selecionados. Isso demonstra equivalência nesta amostra,
mas não mede precisão absoluta contra marcações humanas.

O primeiro par, antes de repetir o teste, deu 36,31/3,55 s. Uma inicialização em
outro processo chegou a 63,12 s. Por isso **não** usei o primeiro par para
anunciar o ganho: carregamento, importações, cache do sistema e aquecimento
alteram bastante a medição. Mesmo os resultados aquecidos variam; são duas
repetições, sem intervalo de confiança estatístico.

Cache só acelera entradas e configurações já processadas. O ganho de 80,3% é
do alinhamento seletivo, não da pipeline inteira. A revisão acrescenta trabalho
na primeira passagem; a reavaliação corrigida inclui inicialização do modelo e
não constitui uma comparação isolada de velocidade entre os dois prompts.

O cache de rastreamento também foi conferido por igualdade dos dados: as três
leituras levaram 6,56 / 0,744 / 0,737 s. A primeira inclui o custo de ler o vídeo
de 1,36 GB para verificar seu hash; o cache do sistema afeta esse tempo. A
economia mediana observada foi 98,6%, válida para repetir o mesmo corte.

Foi exportado o resultado real de 31,78 s: **1080×1920, 30 fps, vídeo de 31,800 s
e áudio de 31,780 s**. A diferença de 20 ms está abaixo de um quadro. Rastreamento
indicou 89% do tempo com foco e 11% com plano aberto; essa proporção é diagnóstico,
não uma medida de acerto do falante. Três quadros foram inspecionados: legendas
legíveis, sem cobrir o rosto. Não foi feita anotação humana de sincronismo labial
ou avaliação quadro a quadro de acerto do enquadramento.
Nos planos próximos, o recorte vertical ficou apertado no topo da cabeça; o
quadro horizontal correspondente preservava essa margem. Portanto, enquadramento
também precisa de calibração, mesmo com exportação tecnicamente válida.

## Resultado editorial

| Janela | Candidatos brutos | Sem revisão | Revisão original | Revisão corrigida |
|---|---:|---:|---:|---:|
| 30–36 min | 4 | 0 | 0 | 0 |
| 60–66 min | 9 | 1 | 0 | 1 |
| 90–96 min | 6 | 1 | 0 | 0 |
| Total | 19 | 2 | 0 | 1 |

O revisor original confundia a fala posterior com o final do trecho e chegou a
inventar objeções factuais. Exemplo: rejeitou a história da Capcom citando a
mudança para Ubisoft, que estava no contexto externo. Em outro caso tratou
Street Fighter Cross Tekken como uma mistura incorreta de jogos. Isso não era
evidência válida para o veto.

A correção separa `selected_clip`, `outside_before` e `outside_after`, informa
explicitamente o último ID interno e impede que o contexto externo seja tratado
como parte do corte. Também orienta a não exigir o encerramento da conversa
inteira, não contestar nomes/opiniões usando conhecimento externo e não
penalizar palavrões por si só. O cache final de curadoria recebeu nova versão;
os checkpoints anteriores foram preservados.

Após a correção, o modelo reconheceu a conclusão do candidato da Capcom, mas
atribuiu nota inferior ao piso. Selecionou um candidato sobre Ubisoft que antes
tinha nota 8 e passou a 8,67 na revisão. O início e o fim desse candidato são
**63:10,94–63:42,72**, duração de 31,78 s.

### Auditoria independente das notas do Qwen

Avaliação feita por Codex a partir da transcrição e contexto, com critérios
registrados antes de ler os resultados editoriais: gancho, compreensão isolada,
conclusão, fidelidade do título e foco. Notas de 0 a 2 por critério. É uma
avaliação subjetiva, não um painel humano cego nem um experimento de audiência.

| Candidato final de algum braço | Gancho | Contexto | Conclusão | Título | Foco | Total |
|---|---:|---:|---:|---:|---:|---:|
| Capcom, 60:58,20–63:12,40; sem revisão | 1 | 2 | 2 | 2 | 1 | 8/10 |
| Nintendo, 90:00,60–90:36,02; sem revisão | 0 | 1 | 0 | 1 | 2 | 4/10 |
| Ubisoft, 63:10,94–63:42,72; revisão corrigida | 0 | 1 | 2 | 2 | 2 | 7/10 |

- **Capcom:** começa nomeando o tema, apresenta crise e recuperação, e termina
  projetando a confiança nos próximos jogos. É compreensível, embora longo e
  com digressões. Seu veto original não se sustentava.
- **Nintendo:** começa com “Tá entendendo?” e termina em “Porque a hora que
  esses caras”. O título promete falar de legado, mas essa parte não conclui.
  A rejeição evita uma falha editorial concreta.
- **Ubisoft:** tem desenvolvimento e resposta coerentes sobre remakes pedidos
  pelos fãs. Porém começa com “E aí agora vai ser o Unimusha”, resíduo do assunto
  anterior. A abertura pede ajuste; a aprovação automática não o tornou perfeito.

Assim, houve redução de finais claramente interrompidos entre os resultados
selecionados (um de dois antes; nenhum no único resultado corrigido). O revisor
também descartou uma história aproveitável da Capcom pelo piso numérico. A
diferença entre as notas dos poucos cortes não sustenta uma porcentagem geral
de “ganho de qualidade”. O mesmo conjunto serviu para detectar e corrigir o
problema; falta validação em episódios independentes.

## Evidências e reprodução

Resultados preservados em:

`data/benchmarks/real-podcast/run-20260918T193912-85c54ba1/`

- `measurements.json`: primeira medição, anterior à correção editorial.
- `alignment-repeated.json`: repetições aquecidas, com inicialização identificada.
- `editorial-*.json`, `raw-*.txt`, `transcript-*.json`: candidatos e transcrições.
- `before-*/review.html`, `after-*/review.html`: páginas de revisão com vídeo fonte.
- `editorial-rubric.json`: critérios e limitações registrados previamente.
- `editor-recheck/run-20260918T194618-462ef50d/`: reavaliação após a correção.
- Dentro dessa reavaliação, `render/run-20260918T194830-8fc5cc8a/` contém vídeo,
  página de revisão, medições de rastreamento/exportação e quadros inspecionados.
- Logs e respostas locais em cache permitem auditar decisões, inclusive erradas.

```bash
.venv/bin/python scripts/benchmark_real_podcast.py
.venv/bin/python scripts/recheck_podcast_editor.py data/benchmarks/real-podcast/run-...
.venv/bin/python scripts/benchmark_podcast_render.py data/benchmarks/real-podcast/run-.../editor-recheck/run-...
```

Novas execuções usam o código corrigido e pastas novas, portanto não reproduzem
o prompt antigo automaticamente. Os registros antigos permanecem disponíveis.
Não foram deletados arquivos nem processados serviços pagos.

Validação do código: 55 testes passaram, incluindo regressão da separação entre
trecho e contexto. Após mudar a versão do cache final, o teste de integração da
CLI foi repetido e passou. Dois avisos de depreciação já existentes do pysubs2.

## Prioridades indicadas pelos dados

1. Refinar início/fim sem perder a conclusão: o candidato da Ubisoft deixa isso
   mais evidente que qualquer nota global do modelo.
2. Calibrar notas e piso editorial com avaliação humana; geração e revisão
   usam escalas instáveis. Não reduzir o piso só para aumentar a quantidade.
3. Verificar vetos com evidência interna, sobretudo a alegação de frase cortada.
4. Validar em outros episódios e gêneros antes de afirmar ganho generalizado.
5. Manter alinhamento seletivo e checkpoints: foram os ganhos objetivos demonstrados.
6. Ajustar margem superior nos planos próximos e validar a câmera em sequência,
   não apenas com estatísticas de tempo em foco.
