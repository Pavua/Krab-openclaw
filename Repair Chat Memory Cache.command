#!/bin/zsh
# Чинит reasoning-мусор в persisted chat-history одним кликом.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "🧠 Санирую persisted chat-memory cache..."
echo "👤 Пользователь: $(whoami)"
echo

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="python3"
fi

set +e
"$PYTHON_BIN" scripts/sanitize_history_cache.py "$@"
EXIT_CODE=$?
set -e

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
echo "Нажми Enter для закрытия окна..."
read -r _
exit $EXIT_CODE
