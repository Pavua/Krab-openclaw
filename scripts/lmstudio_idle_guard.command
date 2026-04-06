#!/bin/bash
# One-click guard для принудительной выгрузки зависших моделей LM Studio.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x "venv/bin/python" ]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧊 LM Studio Idle Guard"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

"$PYTHON_BIN" scripts/lmstudio_idle_guard.py "$@"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Guard завершен успешно."
else
  echo "❌ Guard завершен с ошибкой (код: $EXIT_CODE)."
fi
echo "Отчеты: artifacts/ops/lmstudio_idle_guard_latest.json"
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
