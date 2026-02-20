#!/bin/zsh
# Быстрый запуск параллельного режима Antigravity.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_DIR"

echo "== Krab Parallel Mode: Antigravity Bootstrap =="
echo
echo "1) Проверка ownership overlap..."
python3 scripts/check_workstream_overlap.py || true
echo
echo "2) Открой в Antigravity документы:"
echo "   - docs/EXTERNAL_AGENT_FEED_INDEX_RU.md"
echo "   - docs/NEURAL_PARALLEL_MASTER_PLAN_RU.md"
echo "   - docs/ANTIGRAVITY_START_HERE.md"
echo "   - docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md"
echo "   - docs/ANTIGRAVITY_BACKLOG_V8.md"
echo "   - docs/parallel_execution_split_v8.md"
echo
echo "3) Вставь prompt из docs/ANTIGRAVITY_WORKSTREAM_PROMPT.md"
echo
echo "Готово."
echo "Нажми Enter для закрытия..."
read -r _
