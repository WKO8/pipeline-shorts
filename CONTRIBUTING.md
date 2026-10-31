# Guia de Contribuição - Pipeline Shorts

Seja bem-vindo(a) à comunidade de código aberto do **Pipeline Shorts**! Agradecemos o seu interesse em contribuir para tornar este ecossistema de geração inteligente de vídeos e cortes verticais cada vez mais robusto, rápido e acessível.

Este documento estabelece as diretrizes para configuração de ambiente local, padrões de desenvolvimento, convenções de commits, rotinas de testes e políticas de segurança.

---

## Índice

- [Código de Conduta](#código-de-conduta)
- [Ambiente de Desenvolvimento](#ambiente-de-desenvolvimento)
  - [Pré-requisitos](#pré-requisitos)
  - [Passo a Passo de Instalação](#passo-a-passo-de-instalação)
  - [Modelos Locais e Ollama](#modelos-locais-e-ollama)
- [Padrão de Commits (Conventional Commits)](#padrão-de-commits-conventional-commits)
- [Testes e Validação](#testes-e-validação)
- [Diretrizes de Código e Pull Requests](#diretrizes-de-código-e-pull-requests)
- [Política de Segurança](#política-de-segurança)

---

## Código de Conduta

Ao participar deste projeto, espera-se que todos os colaboradores mantenham um ambiente acolhedor, respeitoso e livre de assédio para todos, independentemente de experiência, identidade de gênero, orientação sexual, deficiência, aparência física ou religião.

---

## Ambiente de Desenvolvimento

### Pré-requisitos

1. **Sistema Operacional:** Linux (Ubuntu 22.04+ ou distribuições compatíveis recomendadas).
2. **Python:** 3.10 ou superior.
3. **FFmpeg:** Compilado com suporte a `libass` e aceleração por hardware NVENC (caso use GPU NVIDIA).
4. **Hardware Recomendado:** GPU NVIDIA com pelo menos 6–8 GB de VRAM (ex.: RTX 3050 ou superior) com drivers proprietários e CUDA funcionais.
5. **Ollama:** Instalado e rodando localmente na porta padrão (`localhost:11434`).

### Passo a Passo de Instalação

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/seu-usuario/pipeline-shorts.git
   cd pipeline-shorts
   ```

2. **Crie e ative o ambiente virtual:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Instale as dependências:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
   *(Caso utilize `uv`, você pode usar `uv pip install -r requirements.txt` para maior velocidade).*

4. **Configure o arquivo de variáveis de ambiente:**
   ```bash
   cp .env.example .env
   ```
   Revise o arquivo `.env` para ajustar os caminhos e parâmetros conforme a sua máquina. Nunca comite o arquivo `.env` com dados privados ou chaves no repositório.

### Modelos Locais e Ollama

O projeto prioriza execução 100% local e sem custos de API:

1. **Baixe e inicialize o modelo do Ollama:**
   ```bash
   ollama pull qwen3.5:4b
   ```
   Certifique-se de que o serviço do Ollama esteja em execução:
   ```bash
   curl http://localhost:11434/api/tags
   ```

2. **Baixe os pesos de tracking e qualidade:**
   Execute o script para validação dos pesos de YuNet e LR-ASD via hash SHA-256:
   ```bash
   python scripts/setup_quality_models.py
   ```

---

## Padrão de Commits (Conventional Commits)

Adotamos a convenção do [Conventional Commits](https://www.conventionalcommits.org/pt-br/v1.0.0/) para manter o histórico de mudanças legível e facilitar a geração automatizada de releases e changelog.

### Estrutura do Commit

```text
<tipo>(<escopo opcional>): <descrição curta no imperativo>

[corpo opcional explicando motivação e detalhes]

[rodapé opcional para referenciar issues ou breaking changes]
```

### Tipos Permitidos

- `feat:` Uma nova funcionalidade ou recurso para o usuário/pipeline.
- `fix:` Correção de um bug ou comportamento inesperado.
- `docs:` Alterações exclusivamente em arquivos de documentação (ex.: README, guias, docstrings).
- `refactor:` Modificação de código que não altera o comportamento externo nem adiciona funcionalidade.
- `test:` Adição, correção ou refatoração de testes automatizados.
- `chore:` Alterações em processos de build, ferramentas auxiliares, dependências ou tarefas rotineiras.

### Exemplos

```bash
git commit -m "feat(curator): adiciona suporte a filtragem por janelas temporais dinâmicas"
git commit -m "fix(alignment): corrige dessincronização de palavras em pausas longas"
git commit -m "docs(contributing): documenta instruções para setup local do Ollama"
git commit -m "test(quality): adiciona testes unitários para cálculo de pontuação ASD"
git commit -m "chore: atualiza dependências do faster-whisper e whisperx"
```

---

## Testes e Validação

Antes de abrir um Pull Request, certifique-se de que toda a suíte de testes passa com sucesso localmente.

### Executando Testes

Execute o `pytest` a partir da raiz do repositório utilizando o executável do ambiente virtual e configurando o `PYTHONPATH`:

```bash
PYTHONPATH=. .venv/bin/pytest
```

Para rodar apenas um módulo específico de testes:

```bash
PYTHONPATH=. .venv/bin/pytest tests/test_quality.py -v
PYTHONPATH=. .venv/bin/pytest tests/test_vertical.py -v
PYTHONPATH=. .venv/bin/pytest tests/test_narrative.py -v
```

Para executar verificações de sintaxe e estilo:
```bash
python -m py_compile main.py
```

### Testando e Validando com Docker

Para validar a aplicação e a interface Web dentro do container idêntico ao ambiente de produção local:

1. **Construir e iniciar o serviço com Docker Compose:**
   ```bash
   docker compose up --build -d
   ```
2. **Acompanhar os logs do container:**
   ```bash
   docker compose logs -f pipeline-shorts
   ```
3. **Verificar a integridade do serviço via endpoint de healthcheck:**
   ```bash
   curl http://localhost:7860/api/health
   ```
4. **Parar os containers após os testes:**
   ```bash
   docker compose down
   ```

---

## Diretrizes de Código e Pull Requests

1. **Branching:** Crie uma branch descritiva a partir da `main`:
   ```bash
   git checkout -b feat/melhoria-na-curadoria
   ```
2. **Qualidade e Estilo:**
   - Siga as convenções de estilo PEP 8.
   - Utilize anotações de tipo (*Type Hints*) sempre que possível.
   - Mantenha comentários e docstrings explicativos e atualizados.
   - Não deixe arquivos temporários, logs ou vídeos pesados no commit (respeite o `.gitignore`).
3. **Submissão do PR:**
   - Abra o Pull Request descrevendo claramente as motivações da mudança, como reproduzir/testar e os resultados obtidos.
   - Mantenha os PRs focados em um único objetivo para facilitar a revisão por pares.

---

## Política de Segurança

A segurança e a privacidade dos dados processados localmente são prioridades absolutas no Pipeline Shorts.

### Reportando Vulnerabilidades

- **Não abra Issues públicas** para relatar vulnerabilidades de segurança, credenciais expostas ou falhas exploráveis.
- Envie um relatório confidencial através dos seguintes canais:
  1. Aba **Security** do repositório no GitHub (opção *Report a vulnerability* via GitHub Private Vulnerability Reporting).
  2. Caso a opção acima não esteja disponível, envie um e-mail direto aos mantenedores do projeto com detalhes técnicos da reprodução.
- No relatório, inclua:
  - Descrição detalhada da vulnerabilidade.
  - Passos reproduzíveis e prova de conceito (PoC), se aplicável.
  - Impacto potencial estimado.
  - Sugestão de correção, caso já tenha elaborado uma solução.

Os mantenedores se comprometem a confirmar o recebimento do relato em até 48 horas úteis e manter o relator informado durante todo o ciclo de mitigação e publicação de correção.
