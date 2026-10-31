#!/bin/bash
set -e
cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Activate or create virtual environment
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  Creating virtual environment...${NC}"
    python3 -m venv .venv
fi

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo -e "${YELLOW}Warning: .venv not found. Running with system python.${NC}"
fi

# Ensure Flask is installed
echo -e "${BLUE}Checking dependencies...${NC}"
python -m pip install flask -q 2>/dev/null || echo "Flask already installed"

echo -e "\n${GREEN}========================================================${NC}"
echo -e "${GREEN}🎬  Pipeline Shorts UI Server${NC}"
echo -e "${GREEN}🌐  Acesse: http://localhost:7860${NC}"
echo -e "${GREEN}========================================================${NC}\n"

python ui/app.py
