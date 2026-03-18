#!/bin/zsh
# Экспортирует полный handoff-пакет для безопасной миграции в новый чат.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "🧩 Экспортирую Anti-413 handoff bundle..."
if [ -f ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="python3"
fi

EXIT_CODE=0
"$PYTHON_BIN" scripts/export_handoff_bundle.py || EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
echo "Нажми Enter для закрытия окна..."
read -r _
exit $EXIT_CODE
