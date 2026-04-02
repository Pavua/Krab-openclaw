#!/bin/bash
# One-click пере-применение локального OpenClaw sessions patch после обновления dist.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🩹 Reapply OpenClaw Sessions Patch"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

"$PYTHON_BIN" scripts/reapply_openclaw_sessions_patch.py "$@"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Локальный patch проверен/пере-применён."
  echo "Рекомендуется перезапустить OpenClaw или весь Krab launcher."
else
  echo "❌ Не удалось пере-применить patch."
fi

read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
