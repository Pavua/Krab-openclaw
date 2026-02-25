#!/bin/zsh
# One-click запуск channel policy smoke для Krab.
# Проверяет критичные сценарии local/cloud/fallback в роутере.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧪 Channel Policy Smoke"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

"$PYTHON_BIN" scripts/channel_policy_smoke.py "$@"
EXIT_CODE=$?

echo
if [[ "$EXIT_CODE" -eq 0 ]]; then
  echo "✅ Smoke завершен успешно."
else
  echo "❌ Smoke завершен с ошибками (код: $EXIT_CODE)."
fi
echo "Отчеты: artifacts/ops/channel_policy_smoke_latest.json"
# В интерактивном запуске (двойной клик) держим окно до Enter.
if [[ -t 0 ]]; then
  read -r "?Нажми Enter, чтобы закрыть окно..."
fi
exit "$EXIT_CODE"
