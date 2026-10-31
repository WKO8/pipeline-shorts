# Qualidade e desempenho dos cortes

Revisão de 18/09/2026. As configurações atuais já priorizam execução local:
Whisper `large-v3-turbo`, `int8_float16`, um render por vez, alinhamento habilitado,
upscaling desabilitado e NVENC `p6`. Não há benchmark que justifique prometer
um ganho percentual ou aumentar a concorrência na GPU.

## Ajustes e critérios

| Configuração | Critério de uso |
| --- | --- |
| `WHISPER_ALIGNMENT=true` | Mantém refinamento das legendas; desativar elimina essa etapa e usa os tempos originais do Whisper. |
| `WHISPER_ALIGNMENT_RETRY_PADDING=0.25` | Uma tentativa adicional somente para segmentos rejeitados. Usa até 250 ms por lado, limitado à metade do intervalo livre até o vizinho. `0` desativa a tentativa. Não resolve fala sobreposta ou texto incorreto. |
| `WHISPER_ALIGNMENT_MODEL` | ID alternativo compatível com o idioma ou pasta **completa** local de um modelo Wav2Vec2. A pasta local evita consultas ao Hub durante o carregamento desse modelo. Vazio preserva a seleção padrão do WhisperX. |
| `RENDER_PARALLEL_JOBS=1` | Manter até medir VRAM durante tracking e render. Paralelismo maior pode disputar memória com os modelos. |
| `FFMPEG_NVENC_PRESET=p6` | Valor efetivo atual. Removida a definição anterior duplicada `p5` em `.env` e `.env.example`. Comparar `p5` e `p6` no mesmo trecho antes de mudar o compromisso entre velocidade e qualidade. |
| `UPSCALING_ENABLED=false` | Evita a etapa adicional; avaliar restauração apenas em fontes que realmente precisem dela. |
| `SILENCE_JUMP_CUTS_ENABLED=false` | Preserva pausas naturais. Ao ativar, o padrão agora é consistente: `-40 dB`, duração mínima `0.8 s`, margem `0.15 s`. O carregador usava incorretamente `0.10 s` quando a duração não estava no `.env`. |
| `CLIP_MIN_SECONDS=30`, `CLIP_MAX_SECONDS=180` | Valores locais mantidos. Reduzir o máximo restringe histórias elegíveis; a curadoria deve selecionar outra história completa, sem cortar a conclusão para caber. |
| `VIRALITY_MIN_SCORE=8.5` | Mantido. A nota é uma estimativa do modelo; revisar gancho, contexto e conclusão no vídeo continua necessário. |

## Diagnóstico do anexo

Os `404` são consultas a nomes opcionais/alternativos: o próprio log confirma
que os pesos carregaram. Mensagens HTTP de baixo nível ficam em `DEBUG`;
warnings e falhas da aplicação continuam visíveis. Isso reduz ruído no log,
mas não acelera o download. Para evitar essas consultas, pode-se apontar
`WHISPER_ALIGNMENT_MODEL` para uma pasta local completa.

O aviso de acesso não autenticado requer uma credencial do usuário, se desejar
limites maiores. Não é necessário token para esses pesos públicos. Autentique
com `hf auth login`, ou exporte `HF_TOKEN` no ambiente antes de iniciar o processo;
não coloque tokens em logs. `HF_HUB_OFFLINE=1` exige que todos os arquivos
necessários já estejam em cache. Referência: [variáveis do Hugging Face](https://huggingface.co/docs/huggingface_hub/en/package_reference/environment_variables).

`backtrack failed` significa que o alinhador não encontrou um caminho para o
texto naquela janela. Nos dados locais, “É difícil, é difícil.” tem somente
0.34 s e uma ocorrência de “Tá.” tem 0.06 s. Isso é compatível com janelas
insuficientes; o aviso sozinho não prova que o texto reconhecido está correto.
O comportamento da biblioteca pode ser conferido no [código do WhisperX](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py).

A integração agora rejeita alinhamento que perde palavras, contém NaN, ultrapassa
a janela além de 40 ms ou sobrepõe palavras. Pequenos excessos de até 40 ms
na conversão de frames CTC são limitados à janela. Uma tentativa limitada pode aproveitar o silêncio
vizinho. Se não funcionar, preserva o texto e os tempos originais; não inventa
uma sincronização. `timing_source` distingue resultado completo, parcial e ASR
original, inclusive quando nenhuma frase pôde ser alinhada. O resumo no log
conta resultados aceitos, recuperados, mistos e mantidos do ASR.

O parser de `.env` agora respeita aspas e comentários usando `python-dotenv`.
Os testes do projeto são executados com `.venv/bin/python -m pytest -q`;
`pytest.ini` exclui da descoberta padrão as suítes de fornecedores em `bin/`.

As alterações passam a valer em novas execuções. Arquivos de transcrição e
vídeos já exportados não são reescritos automaticamente.

## Validação desta revisão

- 34 testes passaram; dois avisos de depreciação preexistentes de `pysubs2`.
- Teste real em CPU, com pesos locais e Hub offline: três segmentos do áudio
  `davy-jones-teste.wav` (125.20–128.16 s) aceitos, incluindo recuperação da
  frase “É difícil, é difícil.” após a primeira tentativa falhar.
- Esse teste verifica execução e integridade temporal, não comprova sincronismo
  perceptivo perfeito nem recuperação de todas as sete ocorrências do log.
- A GPU não estava acessível nesta sessão; não foi medido tempo em CUDA nem
  executado um novo render completo.
