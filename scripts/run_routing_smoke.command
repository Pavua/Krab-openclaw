#!/bin/zsh
# Krab: Run Routing Smoke Test
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || source .venv_krab/bin/activate 2>/dev/null
python3 scripts/routing_smoke.py
echo "\nНажми любую клавишу для выхода..."
read -k 1
