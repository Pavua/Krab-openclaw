#!/bin/bash
# One-click smoke по live-каналам и утечкам служебного текста (macOS .command).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧪 Live Channel Smoke"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

"$PYTHON_BIN" scripts/live_channel_smoke.py "$@"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Smoke завершен успешно."
else
  echo "❌ Smoke завершен с ошибками (код: $EXIT_CODE)."
fi
echo "Отчеты: artifacts/ops/live_channel_smoke_latest.json"
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
