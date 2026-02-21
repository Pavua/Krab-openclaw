#!/bin/zsh
# Проверка пересечений ownership между Codex и Antigravity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"
python3 scripts/check_workstream_overlap.py

echo
echo "Готово. Нажми Enter для закрытия..."
read -r _

