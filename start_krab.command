#!/bin/bash
# -----------------------------------------------------------------------------
# 🦀 Krab AI Userbot - Launch Script
# -----------------------------------------------------------------------------
# This is the main entry point for running Krab in interactive mode.
# It ensures the virtual environment is active and sets up the execution context.
# -----------------------------------------------------------------------------

# Navigate to the project root directory
cd "$(dirname "$0")"

# ANSI Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Header
echo -e "${BLUE}=======================================${NC}"
echo -e "${GREEN}   🦀 KRAB AI: AUTONOMOUS AGENT v7.6   ${NC}"
echo -e "${BLUE}=======================================${NC}"

find_core_pids() {
    {
        pgrep -f -- "python(.+)?src/main.py" || true
        pgrep -f -- "python(.+)?-m src.main" || true
    } | tr ' ' '\n' | sed '/^$/d' | sort -u
}

# Check for .env file
if [ ! -f ".env" ]; then
    echo -e "${RED}⚠️  WARNING: .env file not found!${NC}"
    echo "Creating from .env.example..."
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}✅ Created .env. Please configure it!${NC}"
    else
        echo -e "${RED}❌ .env.example also missing! Cannot proceed.${NC}"
        exit 1
    fi
fi

# Check for Virtual Environment
if [ ! -d ".venv" ]; then
    echo -e "${RED}❌ Virtual environment (.venv) not found!${NC}"
    echo "Please run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Launch
echo -e "${BLUE}🚀 Starting Krab Core...${NC}"
echo "Logs structure: logs/krab.log"
echo "Press Ctrl+C to stop."
echo ""

# Защита от двойного старта:
# если ядро уже запущено, новый экземпляр не стартуем.
if [[ "${KRAB_ALLOW_DUPLICATE_START:-0}" != "1" ]]; then
    CORE_PIDS="$(find_core_pids)"
    if [[ -n "$CORE_PIDS" ]]; then
        echo -e "${RED}⚠️  Ядро уже запущено. Второй экземпляр не стартую.${NC}"
        echo "Активные PID:"
        echo "$CORE_PIDS"
        echo "Если нужен перезапуск, запусти: ./full_restart.command"
        exit 0
    fi
fi

# Execute
./.venv/bin/python3 -m src.main
