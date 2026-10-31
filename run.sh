#!/bin/bash
# ============================================================
#  run.sh — Inicia a Interface Web da Pipeline
#  Uso: ./run.sh
# ============================================================

set -euo pipefail
cd "$(dirname "$0")"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}========================================================${NC}"
echo -e "${BLUE}🎬  Pipeline Shorts UI${NC}"
echo -e "${BLUE}========================================================${NC}\n"

# Create or activate venv
if [ ! -d ".venv" ]; then
    echo -e "${YELLOW}⚠️  Creating virtual environment...${NC}"
    python3 -m venv .venv
fi

echo -e "${BLUE}Activating environment...${NC}"
source .venv/bin/activate

# Ensure dependencies
echo -e "${BLUE}Checking dependencies...${NC}"
python -m pip install flask -q 2>/dev/null || true

# Optional: Try to start bgutil PO Token server (for yt-dlp JS challenges)
start_bgutil_optional() {
    BGUTIL_PORT=4416
    
    # Check if already running
    if curl -s "http://127.0.0.1:${BGUTIL_PORT}/" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ bgutil server already running on port ${BGUTIL_PORT}${NC}"
        return 0
    fi
    
    # Skip if node/npm not available
    if ! command -v node &> /dev/null || ! command -v npm &> /dev/null; then
        echo -e "${YELLOW}ℹ️  bgutil PO Token server optional (node/npm not found)${NC}"
        echo -e "${YELLOW}    Pipeline will use yt-dlp without it, but may be slower on YouTube${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}Starting bgutil PO Token server (optional)...${NC}"
    
    BGUTIL_DIR="/tmp/bgutil-server"
    BGUTIL_REPO="https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git"
    
    if [ ! -d "${BGUTIL_DIR}/server" ]; then
        git clone --depth=1 "${BGUTIL_REPO}" "${BGUTIL_DIR}" -q 2>/dev/null || {
            echo -e "${YELLOW}ℹ️  Could not clone bgutil, continuing without it${NC}"
            return 0
        }
    fi
    
    if [ ! -d "${BGUTIL_DIR}/server/node_modules" ]; then
        (cd "${BGUTIL_DIR}/server" && npm install --silent 2>/dev/null) || return 0
    fi
    
    if [ ! -f "${BGUTIL_DIR}/server/build/main.js" ]; then
        (cd "${BGUTIL_DIR}/server" && npx -y tsc --silent 2>/dev/null || npx tsc 2>/dev/null) || return 0
    fi
    
    (cd "${BGUTIL_DIR}/server" && node build/main.js) &
    BGUTIL_PID=$!
    
    # Wait for startup (max 3s)
    for i in $(seq 1 3); do
        sleep 1
        if curl -s "http://127.0.0.1:${BGUTIL_PORT}/" >/dev/null 2>&1; then
            echo -e "${GREEN}✓ bgutil server started on port ${BGUTIL_PORT}${NC}"
            return 0
        fi
    done
    
    echo -e "${YELLOW}ℹ️  bgutil server not responding, continuing without it${NC}"
    return 0
}

# Cleanup on exit
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Start optional bgutil
start_bgutil_optional || true

# Start UI Server
echo ""
echo -e "${GREEN}========================================================${NC}"
echo -e "${GREEN}🎬 Pipeline Shorts UI Server${NC}"
echo -e "${GREEN}🌐 Open: ${YELLOW}http://localhost:7860${NC}"
echo -e "${GREEN}========================================================${NC}"
echo -e "${GREEN}Press Ctrl+C to stop${NC}\n"

python ui/app.py
