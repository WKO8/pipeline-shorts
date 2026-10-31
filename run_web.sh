#!/bin/bash
set -e
cd "$(dirname "$0")/web"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "\n${GREEN}========================================================${NC}"
echo -e "${GREEN}🎯  Pipeline Shorts Web — Auditor de Viralidade Next.js${NC}"
echo -e "${GREEN}🌐  Local: http://localhost:3000${NC}"
echo -e "${GREEN}🌍  Domínio configurado: ${WEB_DOMAIN:-http://localhost:3000}${NC}"
echo -e "${GREEN}========================================================${NC}\n"

pnpm dev
