# Produção automática de Shorts da Vó Sem Filtro

Fluxo completo desta pasta:

```
roteiro (gancho + ação)  →  geração no Google Flow  →  aprovação dos takes
        ↓                                                      ↑
   template de prompt                                aprovar / pedir de novo
                                                              ↓
              edição (corte de silêncio + gancho + legendas)  →  publicação
                    (presets exatos do seu Shotcut)          (YouTube / TikTok / ou só a pasta)
```

O canal é o [Vó Sem Filtro 👵🔥](https://www.youtube.com/@VoSemFiltroIA) — personagem
fictícia de IA em Shorts verticais de conselho de relacionamento. A mesma estrutura serve
para qualquer roteiro A1–A15 da pasta da personagem.

## Como funciona cada etapa

| Etapa | O que roda | Observações |
|---|---|---|
| 1. Geração | `gflow-cli` dirigindo um Chromium autenticado no Flow | O Flow **não tem API pública** (os endpoints são protegidos por reCAPTCHA Enterprise; token programático recebe 403). O `gflow` clica de verdade na interface, então os créditos gastos são os da assinatura Google AI |
| 2. Aprovação | página local `http://localhost:7860/vsf-produce` ou `cli aprovar` | cada take tem preview; aprovar ou pedir de novo |
| 3. Edição | `melt` (motor embutido no Shotcut) rodando um `.mlt` gerado com os **seus presets** | corte de silêncio inicial, gancho (Text:Simples/Anton) e legendas (Subtitles/Montserrat Black) idênticos ao seu export manual |
| 4. Publicação | YouTube Data API v3 (`videos.insert`) / TikTok Studio dirigido por navegador | YouTube é automático; TikTok sai **privado** ("Somente você") — ver a seção TikTok |

Cada etapa é um comando e pode ser rodada isolada: se a geração estiver de molho, você
gera/baixa o clipe na mão no Flow e o resto do pipeline continua igual.

## Preparação (uma vez)

```bash
# 1. driver do Flow (CLI + navegador de verdade)
uv tool install gflow-cli
gflow auth login --browser chrome        # abre o Chrome; autentique ATÉ o editor do Flow abrir
gflow auth status                        # deve dizer: Flow session verified (<conta>)

# 2. publicação no YouTube (aponta para a credencial OAuth que já existe no projeto de analytics)
export VSF_YT_CLIENT_SECRET=.../vosemfiltro-analytics/data/client_secret.json
export VSF_YT_TOKEN=.../vosemfiltro-analytics/data/token.json

# 3. destino dos vídeos (padrão já é este)
export VSF_OUTPUT_DIR=~/Videos/VoSemFiltro/Edited-Videos

# 4. TikTok (uma vez): login manual no perfil dedicado do Playwright
.venv/bin/python -m src.vsf_produce.cli tiktok-login
```

Nada disso guarda senha: o `gflow` mantém a sessão em
`~/.local/share/gflow-cli/profile_default` e o TikTok em
`~/.local/share/vsf-produce/tiktok-profile`. Os caminhos dos segredos do YouTube são
apontados por variável de ambiente; os arquivos nunca são copiados para cá.

## Assets padrão da personagem (character-sheet + scenario-sheet)

As folhas da pasta `~/Pictures/A VoSemFiltro(Vo-Zulmira)-Personagem-IA` são o **padrão**
de referência de imagem:

| Arquivo | Papel |
|---|---|
| `character-sheet.png` | folha de personagem (a mesma do `vo-sem-filtro-character-sheet-2.png`) |
| `scenario-sheet.png` | folha de cenário (cozinha, poltrona, relógio de galo) |
| `full-body.png`, `close-up-rosto.png`, `frame-inicial-dia-1.png` | apoio, usados quando você pedir |

Todo roteiro novo já nasce com essas duas folhas como referência (`VSF_ASSETS_DIR` aponta
para outro lugar, se a pasta mudar):

```bash
$PY -m src.vsf_produce.cli novo --slug x --semana 4 --gancho "G" --acao "..." # já vem com as 2 folhas
$PY -m src.vsf_produce.cli novo ... --sem-referencias-padrao                  # para não usar nenhuma
$PY -m src.vsf_produce.cli novo ... --referencia ~/Pictures/.../close-up-rosto.png
```

### O que foi verificado (e a limitação desta conta)

Com as folhas marcadas, o comando muda de `video t2v` para **`video r2v … --ref <folha1>
--ref <folha2>`**. Ao rodar de verdade, o `gflow` **abortou com
`MentionIndexUnavailableError` (exit 29)**: contas servidas por `flow.google.com` não
conseguem carregar o **catálogo de ativos** do Flow, e esse catálogo é necessário tanto
para as `@tags` do prompt quanto para subir arquivo de referência. O mesmo acontece com
`@menções` no texto.

Por isso o driver tem **recuo automático em duas camadas** (`flow_driver.gerar`):

1. tenta exatamente o que o roteiro pede (com referências e/ou `@tags`);
2. se o erro for só o catálogo, refaz o envio **sem referências e sem `@`**, mantendo o
   `BLOCO A` + a ação — a Vó continua correta porque a descrição em inglês é detalhada e o
   vídeo nasce dentro do seu projeto do Flow;
3. o abort acontece **antes do envio**, então a tentativa que falhou **não gasta crédito**
   (a mensagem no terminal diz isso explicitamente). Erro de outra natureza não é repetido.

O teste real (`a2-filho-so-liga`, roteiro A2 da sua biblioteca) saiu com essa recuo: o
clipe veio com a Vó **segurando o fone**, na cozinha amarela, com o relógio de galo — a
ação do roteiro. As folhas ficam registradas no JSON do roteiro, prontas para o dia em que
a conta passar a carregar o catálogo.

Se você quiser a consistência vinda da folha de imagem **agora**, o caminho que funciona é
gerar/baixar o clipe no editor do Flow (onde você mesmo anexa os assets) e registrá-lo:

```bash
$PY -m src.vsf_produce.cli take a2-filho-so-liga ~/Downloads/clipe-do-flow.mp4 --aprovar
$PY -m src.vsf_produce.cli editar a2-filho-so-liga      # daqui para baixo é igual
```

## O template de prompt (`prompts/vsf-prompt-base.txt`)

O arquivo é seu, com blocos `== Nome ==`. Nomes antigos (`Initial Frame`, `Thubnail`,
`Video`) continuam aceitos.

| Bloco | Para que serve |
|---|---|
| `BLOCO A` | a descrição fixa em inglês: personagem, cenário, voz, enquadramento ("No subtitles, no on-screen text, no watermark") |
| `Acao` | `[BLOCO A] [ACAO]` — é o formato operacional: **prompt do vídeo = BLOCO A + a ação/fala daquele roteiro** |
| `Frame inicial` | bloco com `[CENA]`, para gerar o quadro inicial (`gflow image`) |
| `Thumbnail` | bloco com `[EXPRESSAO]`; deixando a parte de baixo livre para o texto |
| `Referencias` | as `@tags` (Character/Voice/folha/quadro) — sujeitas à limitação acima |
| `Descricao` | descrição do post com `[LEGENDA]` e `[SET]` |
| `Hashtags` | conjuntos A1/A2/A3 |

```bash
PY=.venv/bin/python

# cria o roteiro: gancho = texto NA TELA; --acao = a fala/gesto em inglês; --legenda/--set
# montam a descrição padrão do template (legenda + CTA + "🤖 Vó criada com IA" + hashtags)
$PY -m src.vsf_produce.cli novo --slug a2-filho-so-liga --semana 4 \
  --gancho "SÓ LIGA QUANDO PRECISA" \
  --acao 'She holds the receiver of an old landline phone and says: "Filho que so liga quando precisa? ..."' \
  --cena "segurando o fone de um telefone antigo" --expressao "olhar desconfiado" \
  --legenda "Telefone funciona nos dois sentidos 📞 Já passou por isso?" --set A2 \
  --modelo omni-flash --duracao 10 --resolucao 720p

$PY -m src.vsf_produce.cli prompt a2-filho-so-liga --bloco video   # só mostra o que vai ao Flow
$PY -m src.vsf_produce.cli prompt a2-filho-so-liga --bloco frame
$PY -m src.vsf_produce.cli prompt a2-filho-so-liga --bloco thumbnail
```

O prompt usado em cada take fica gravado no JSON do roteiro (rastreável para regenerar).

## Passo a passo do dia a dia

```bash
PY=.venv/bin/python
export VSF_OUTPUT_DIR=~/Videos/VoSemFiltro/Edited-Videos

# 1. um roteiro por vídeo (nada é gasto aqui)
$PY -m src.vsf_produce.cli novo --slug <slug> --semana <N> --gancho "<GANCHO NA TELA>" \
   --acao "<ação em inglês>" --legenda "<legenda>" --set <A1|A2|A3> \
   --modelo omni-flash --duracao 10 --resolucao 720p

# 2. confira o comando e o custo SEM gastar
$PY -m src.vsf_produce.cli gerar <slug> --dry-run

# 3. gere (gasta crédito) — o saldo da conta não é exposto pela CLI
$PY -m src.vsf_produce.cli gerar <slug> --quantidade 1
$PY -m src.vsf_produce.cli gerar <slug> --quantidade 2        # 2 takes para escolher

# 4. aprove na página local (http://localhost:7860/vsf-produce) ou na CLI
$PY -m src.vsf_produce.cli status
$PY -m src.vsf_produce.cli aprovar <slug> take1.mp4

# 5. edição: corte de silêncio + gancho + legendas com os presets do Shotcut
$PY -m src.vsf_produce.cli editar <slug> --srt <caminho.srt>   # --srt é opcional

# 6. publique (ou só guarde o arquivo — ele já está na pasta)
$PY -m src.vsf_produce.cli publicar <slug> --privacidade private    # YouTube
$PY -m src.vsf_produce.cli tiktok <slug> --acao privado            # TikTok "Somente você"
```

Os vídeos saem em `~/Videos/VoSemFiltro/Edited-Videos/Semana<N>/` com o padrão
`vosemfiltro-semana<N>-<slug>.mp4` — é só postar na mão quando quiser. Se preferir não
publicar por API, pare no passo 5: o arquivo já está pronto no seu padrão de edição.

Outros comandos: `motor` (testa se este ambiente desenha texto), `creditos`, `take`
(registra um clipe baixado à mão), `tiktok-login`.

## Aprovação

* Página local: `http://localhost:7860/vsf-produce` — cada take com preview, botões
  Aprovar / Regenerar (com motivo) e o log das ações longas (thread + polling).
* CLI: `status` lista os takes com o estado; `aprovar <slug> take2.mp4` marca o escolhido;
  `gerar <slug> --quantidade 2` cria alternativas para comparar (cada take = 15 créditos).
* `take <slug> <arquivo> --aprovar` cobre o caso "gerei/baixei no Flow à mão".

## Edição (presets do Shotcut, sem tradução)

Lidos dos seus projetos (`~/.var/app/org.shotcut.Shotcut/data/Meltytech/Shotcut/autosave/*.mlt`):

| Elemento | Fonte | Tamanho | Contorno | Caixa |
|---|---|---|---|---|
| Gancho | Anton | 173 (pointSize 130) | 35, `#aa000000` | `-11 559 1080 621`, valign bottom (base y=1180), centralizado |
| Legendas | Montserrat Black | 105 (pointSize 79) | 20, `#aa000000` | `5 841 1071 599` (y 841–1440) |
| Export | — | 1080x1920 @ 60 fps, NVENC p6, ~10 Mbps + AAC 256k | | |

Corte de silêncio: `silencedetect noise=-35dB:d=0.3`, mantendo 0,10 s de respiro no início
(o take do Flow costuma ter 0,6–1,0 s de silêncio — os clipes antigos do canal também).

O gancho fica na faixa livre **entre o rosto e as legendas** (base y=1180, medido em
y 973–1121 na render final; a legenda começa em y 1208 → ~87 px de folga). O preset
original do Shotcut terminava em y=1518 e cobria a legenda de baixo.

Sobrescreva por roteiro em `presets` no JSON (ex.: `{"gancho": {"outline": 12}}`).

Duas armadilhas já medidas:

* **Contorno e tamanho não são pixels do ASS.** O `outline` do MLT não corresponde a px do
  ffmpeg; por isso a edição roda pelo motor do Shotcut em vez de ser traduzida para ASS.
* **O gancho longo é cortado nas laterais.** O filtro encaixa o texto na caixa de 1080 px
  (a partir de x=-11); `quebrar_gancho()` divide em duas linhas equilibradas.

## Publicação

| Destino | Mecanismo | Estado |
|---|---|---|
| Pasta local | `VSF_OUTPUT_DIR/Semana<N>/vosemfiltro-semana<N>-<slug>.mp4` | sempre — nada depende de rede |
| YouTube | `videos.insert` (escopo `youtube.force-ssl` do app de analytics) | automático; cota ~100 uploads/dia; `--privacidade private` ou `--agendar` |
| TikTok | TikTok Studio dirigido por navegador, visibilidade fixada em **"Somente você"** | automático, mas o vídeo nunca vai ao público sem a sua ação |

```bash
$PY -m src.vsf_produce.cli publicar <slug> --dry-run    # mostra a descrição que vai
$PY -m src.vsf_produce.cli publicar <slug>              # privado por padrão
$PY -m src.vsf_produce.cli tiktok <slug> --acao rascunho  # preenche e não publica
```

A descrição sai do bloco `Descricao` do template: legenda + "Manda teu caso aqui…" +
"🤖 Vó criada com IA" + o conjunto de hashtags escolhido.

### Por que o TikTok não usa a API oficial

| Caminho | Dá para deixar privado? | Situação |
|---|---|---|
| **Direct Post** (`video.publish`) | **Não.** Cliente não auditado só posta em conta privada (`unaudited_client_can_only_post_to_private_accounts`) | exigiria auditoria do app |
| **Upload/Inbox** (`video.upload`) | Sim: vai para os rascunhos; máx. **5 pendentes por 24 h** | exige app aprovado + escopo + OAuth |
| **TikTok Studio dirigido por navegador** | Sim, "Somente você" | **implementado**: sem aprovação, sujeito a quebra quando o Studio muda de DOM |

Falhas gravam screenshot + HTML em `data/vsf_produce/tiktok_debug/` para consertar a
seleção de elementos.

## Créditos (tabela do Google)

Google AI Pro = **50 créditos/dia + 1.000/mês** (o mensal não acumula).

| Modelo | Créditos por geração |
|---|---|
| Veo 3.1 Lite / Lite (lower priority) | 10 / 0 (só Ultra) |
| Veo 3.1 Fast | 20 |
| Veo 3.1 Quality | 100 |
| Gemini Omni Flash 720p | 7 / 10 / 12 / 15 (4 / 6 / 8 / 10 s) |
| Gemini Omni Flash 360p | 4 / 5 / 6 / 7 |
| Upscale para 1080p | **0** para assinantes |

Os clipes brutos do canal são 720x1280 @ 24 fps com 10,0 s — a assinatura de `omni-flash`
a 10 s (**15 créditos**). Com 2–3 takes por vídeo aprovado, o teto prático é de ~2–4
vídeos/dia. **O limite é crédito, não código.**

## Automação (cron / SSH)

O `melt` do flatpak **desenha texto sem servidor gráfico** — verificado agora com o teste
determinístico: `DISPLAY` removido **e** sem `xvfb-run` no comando, o probe
(`editor_mlt.texto_aplicado`) desenhou os glifos. Logo, cron não precisa de prefixo nenhum:

```bash
$PY -m src.vsf_produce.cli editar <slug>        # roda em cron/SSH sem xvfb
```

Duas ressalvas honestas:

* O aviso do melt *"The MLT Qt module requires a X11 or Wayland environment"* **aparece no
  stderr mesmo quando o texto sai certo** — não use o log como sinal. O que vale é o
  `cli motor`, que renderiza um quadro de fundo cinza chapado e conta os glifos brancos.
* `xvfb-run -a` continua sendo o prefixo certo para **automação de navegador com janela** —
  ou seja, o TikTok:

```bash
xvfb-run -a $PY -m src.vsf_produce.cli tiktok <slug> --acao privado
```

O render **recusa-se a gravar um vídeo sem texto** em vez de entregar um arquivo
silenciosamente quebrado (é o mesmo teste do `motor`, rodado antes de cada render).

Para publicar depois de um render de madrugada, o alvo precisa ser um canal conectado (o
Telegram, por exemplo): o cron do TUI salva a saída, mas não entrega nesta sessão.

## Armadilhas medidas

* **`/tmp` não existe para o melt do flatpak.** O sandbox enxerga o home (e o próprio
  `/tmp`). Passe caminhos sob `$HOME`; o sintoma de caminho inacessível é
  `Failed to load ….mlt`.
* **O `.mlt` precisa de caminho absoluto do clipe.** Com caminho relativo o melt abre o
  projeto sem o vídeo e sai um **arquivo branco de ~67 KB** (só áudio). `montar_projeto`
  já resolve para absoluto.
* **O filtro de legenda usa o tempo da TIMELINE, não o do clipe.** Como o SRT é transcrito
  do clipe inteiro e a timeline começa no ponto do corte, toda legenda sairia adiantada em
  exatamente o tempo cortado (0,6–1,0 s). `editor_mlt.deslocar_srt` subtrai o offset ao
  embutir o SRT (cues que caem dentro do corte são descartadas). Provado com um caso
  controlado: clipe de 6 s (3 s de silêncio + 3 s de tom), corte de 2,9 s, cue em 3,0–4,0 s
  → o texto **não** apareceu; cue em 0,2–1,0 s → apareceu em 0,4 e 0,9 e sumiu em 2,0.
* **`--resolucao` só existe para `omni-flash`.** Em modelo Veo o gflow aborta antes de
  enviar; a flag só é adicionada quando o modelo é omni-flash.
* **Roteiro sem prompt é recusado** (`montar_comando`): mandaria só `@Vó Zulmira` ao Flow e
  gastaria crédito sem descrever nada.
* **`@menções`/`--ref` não resolvem nesta conta** (ver a seção dos assets): o recuo cobre,
  e o abort não consome crédito.

## Troubleshooting

| Sintoma | Causa e saída |
|---|---|
| `MentionIndexUnavailableError` (exit 29) | catálogo de ativos do Flow inacessível nesta conta. Automático: refaz sem referências/`@`. Nada foi gasto. Para usar a folha de imagem, gere no Flow e registre com `take` |
| `gflow credits …` falha | a conta servida por `flow.google.com` não expõe saldo pela CLI (issue #795 do gflow). Confira os créditos no painel do Google AI |
| `AuthExpiredError` / `outcome=google_session_only` | sessão do Flow caiu: `gflow auth login --browser chrome` e vá até o editor do Flow abrir (o perfil real necessário é o do `gflow`, não o do seu Brave) |
| `ProfileLockedError` | sobrou um lock do perfil: feche processos do gflow/Chromium e apague `SingletonLock` no diretório do perfil |
| Vídeo de ~67 KB ou quadro branco | o `.mlt` não achou o clipe (caminho relativo ou arquivo fora do home por causa do flatpak) |
| Render sem gancho/legenda | o `cli motor` falhou: rode-o para ver. O melt do flatpak desenha texto sem display, então isso aponta para outra causa (fonte ausente no sistema, `.mlt` inacessível ao sandbox) |
| Legenda fora de sincronia | SRT com tempos do clipe inteiro: o deslocamento pelo corte já é automático a partir de agora; se passar um `--srt` externo gerado do clipe inteiro, ele recebe o mesmo deslocamento |
| TikTok não sobe | rode `tiktok-login` de novo; veja o screenshot em `data/vsf_produce/tiktok_debug/` |

## Estrutura

```
src/vsf_produce/
├── presets.py       # presets do Shotcut (gancho/legenda) e perfis nomeados
├── spec.py          # roteiro em JSON + estado de aprovação dos takes
├── silence.py       # detecção do silêncio inicial (silencedetect)
├── editor_mlt.py    # gera o projeto .mlt, desloca o SRT e renderiza com melt
├── flow_driver.py   # geração no Flow via gflow-cli (custos, recuo de referências)
├── producao.py      # orquestração: gerar → editar → publicar
├── prompt.py        # template de prompt (BLOCO A, ação, frame, thumbnail, hashtags)
├── referencias.py   # assets padrão (character-sheet, scenario-sheet)
├── publisher.py     # upload no YouTube (stdlib, videos.insert)
├── tiktok_driver.py # upload privado no TikTok Studio (Playwright, perfil dedicado)
├── prompts/vsf-prompt-base.txt   # SEU template de prompt
└── cli.py           # linha de comando
ui/vsf_produce_routes.py   # API + página local de aprovação
tests/test_vsf_produce.py  # 66 contratos do fluxo (sem rede e sem gasto de crédito)
tests/test_vsf_tiktok.py   # contratos do TikTok (rede só com VSF_TIKTOK_E2E=1)
```

Dados de execução em `data/vsf_produce/` (fora do git):

```
roteiros/<slug>.json          # roteiro, prompt, presets, estado dos takes
roteiros/<slug>/legenda.srt   # transcrição usada na edição
roteiros/<slug>/takes/*.mp4   # takes brutos (aprovado = escolhido)
```
