#!/bin/bash
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"

echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}🎬  Pipeline Shorts — Quick Start${NC}"
echo -e "${BLUE}========================================================${NC}"
echo ""

# Check if .venv exists
if [ ! -d "$VENV_PATH" ]; then
    echo -e "${YELLOW}⚠️  Virtual environment not found. Creating...${NC}"
    python3 -m venv "$VENV_PATH"
    source "$VENV_PATH/bin/activate"
    python -m pip install --upgrade pip setuptools wheel -q
    python -m pip install -r "$PROJECT_ROOT/requirements.txt" -q
    echo -e "${GREEN}✓ Environment created${NC}"
else
    source "$VENV_PATH/bin/activate"
    echo -e "${GREEN}✓ Environment activated${NC}"
fi

echo ""
echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}Escolha uma opção:${NC}"
echo -e "${BLUE}========================================================${NC}"
echo ""
echo -e "${YELLOW}1)${NC} Iniciar Interface Web (UI)"
echo -e "   → Acesse http://localhost:7860"
echo ""
echo -e "${YELLOW}2)${NC} Processar vídeo YouTube com curadoria"
echo ""
echo -e "${YELLOW}3)${NC} Processar intervalo específico (sem Ollama)"
echo -e "   → Exemplo: python main.py --url URL --specific-moment 01:30:00-01:35:00"
echo ""
echo -e "${YELLOW}4)${NC} Re-renderizar clip com legendas editadas"
echo -e "   → Exemplo: python main.py --rerender --subtitles FILE.ass --clipe FILE.mp4"
echo ""
echo -e "${YELLOW}5)${NC} Editar configuração (.env)"
echo -e "   → Abre o editor de texto"
echo ""
echo -e "${YELLOW}6)${NC} Ver estrutura de outputs"
echo -e "   → Mostra arquivos gerados"
echo ""
echo -e "${YELLOW}0)${NC} Sair"
echo ""
echo -n -e "${GREEN}Digite sua opção (0-6):${NC} "
read -r OPTION

case $OPTION in
    1)
        echo -e "\n${BLUE}Iniciando Interface Web...${NC}\n"
        if command -v flask &> /dev/null; then
            python "$PROJECT_ROOT/ui/app.py"
        else
            python -m pip install flask -q 2>/dev/null || true
            python "$PROJECT_ROOT/ui/app.py"
        fi
        ;;
    2)
        echo -e "\n${YELLOW}Opção: Processar vídeo YouTube com curadoria${NC}\n"
        echo -e "${BLUE}Exemplos:${NC}"
        echo ""
        echo -n -e "${GREEN}Digite a URL do YouTube:${NC} "
        read -r YOUTUBE_URL
        
        if [ -z "$YOUTUBE_URL" ]; then
            echo -e "${RED}✗ URL vazia${NC}"
            exit 1
        fi
        
        echo -n -e "${GREEN}Profile (1/2) [1]:${NC} "
        read -r PROFILE
        PROFILE=${PROFILE:-1}
        
        echo -n -e "${GREEN}Max clips [5]:${NC} "
        read -r MAX_CLIPS
        MAX_CLIPS=${MAX_CLIPS:-5}
        
        echo -e "\n${BLUE}Iniciando pipeline...${NC}\n"
        python "$PROJECT_ROOT/main.py" \
            --url "$YOUTUBE_URL" \
            --profile "$PROFILE" \
            --max-clips "$MAX_CLIPS" \
            --verbose
        ;;
    3)
        echo -e "\n${YELLOW}Opção: Processar intervalo específico${NC}\n"
        echo -e "${BLUE}Formatos válidos:${NC}"
        echo "  30-90 (segundos)"
        echo "  00:01:30-00:02:00 (HH:MM:SS)"
        echo ""
        echo -n -e "${GREEN}Digite a URL do YouTube:${NC} "
        read -r YOUTUBE_URL
        
        if [ -z "$YOUTUBE_URL" ]; then
            echo -e "${RED}✗ URL vazia${NC}"
            exit 1
        fi
        
        echo -n -e "${GREEN}Digite o intervalo (ex: 30-90 ou 00:01:30-00:02:00):${NC} "
        read -r MOMENT
        
        if [ -z "$MOMENT" ]; then
            echo -e "${RED}✗ Intervalo vazio${NC}"
            exit 1
        fi
        
        echo -e "\n${BLUE}Iniciando processamento...${NC}\n"
        python "$PROJECT_ROOT/main.py" \
            --url "$YOUTUBE_URL" \
            --specific-moment "$MOMENT" \
            --profile 3 (default) (default) \
            --separate-subtitles \
            --verbose
        ;;
    4)
        echo -e "\n${YELLOW}Opção: Re-renderizar clip${NC}\n"
        echo -e "${BLUE}Arquivos disponíveis:${NC}\n"
        
        echo "Arquivos .ass (legendas):"
        find "$PROJECT_ROOT/data" -name "*.ass" -type f 2>/dev/null | nl
        
        echo ""
        echo -n -e "${GREEN}Caminho do arquivo .ass:${NC} "
        read -r ASS_FILE
        
        if [ ! -f "$ASS_FILE" ]; then
            echo -e "${RED}✗ Arquivo não encontrado: $ASS_FILE${NC}"
            exit 1
        fi
        
        echo ""
        echo "Clips disponíveis:"
        find "$PROJECT_ROOT/data/clips" -name "*.mp4" -type f 2>/dev/null | nl
        
        echo ""
        echo -n -e "${GREEN}Caminho do clip .mp4:${NC} "
        read -r CLIP_FILE
        
        if [ ! -f "$CLIP_FILE" ]; then
            echo -e "${RED}✗ Arquivo não encontrado: $CLIP_FILE${NC}"
            exit 1
        fi
        
        echo -n -e "${GREEN}Recalcular tracking? (s/n) [n]:${NC} "
        read -r RETRACK
        
        echo -e "\n${BLUE}Iniciando re-render...${NC}\n"
        
        CMD="python $PROJECT_ROOT/main.py --rerender --subtitles '$ASS_FILE' --clipe '$CLIP_FILE'"
        if [ "$RETRACK" = "s" ] || [ "$RETRACK" = "S" ]; then
            CMD="$CMD --retrack"
        fi
        eval "$CMD"
        ;;
    5)
        echo -e "\n${YELLOW}Opção: Editar .env${NC}\n"
        
        # Try different editors
        if command -v nano &> /dev/null; then
            nano "$PROJECT_ROOT/.env"
        elif command -v vi &> /dev/null; then
            vi "$PROJECT_ROOT/.env"
        else
            echo -e "${RED}✗ Nenhum editor disponível. Abra manualmente:${NC}"
            echo "  $PROJECT_ROOT/.env"
            exit 1
        fi
        
        echo -e "\n${GREEN}✓ Configuração salva${NC}"
        ;;
    6)
        echo -e "\n${YELLOW}Opção: Ver estrutura de outputs${NC}\n"
        
        echo -e "${BLUE}Arquivos gerados:${NC}\n"
        
        echo "📹 Videos (data/clips/):"
        find "$PROJECT_ROOT/data/clips" -name "*.mp4" 2>/dev/null | head -10 || echo "  Nenhum encontrado"
        
        echo ""
        echo "📝 Legendas (data/subtitles/):"
        find "$PROJECT_ROOT/data/subtitles" -name "*.ass" 2>/dev/null | head -10 || echo "  Nenhuma encontrada"
        
        echo ""
        echo "📄 Transcrições (data/transcripts/):"
        find "$PROJECT_ROOT/data/transcripts" -name "*.json" 2>/dev/null | head -10 || echo "  Nenhuma encontrada"
        
        echo ""
        echo "🎵 Áudio (data/audio/):"
        find "$PROJECT_ROOT/data/audio" -name "*.wav" -o -name "*.mp3" 2>/dev/null | head -10 || echo "  Nenhum encontrado"
        
        echo ""
        du -sh "$PROJECT_ROOT/data"/* 2>/dev/null | sort -rh
        ;;
    0)
        echo -e "\n${GREEN}Até logo!${NC}\n"
        exit 0
        ;;
    *)
        echo -e "\n${RED}✗ Opção inválida${NC}\n"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}✓ Concluído!${NC}"
echo ""
