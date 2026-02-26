#!/bin/bash
# One-click R20 Merge Gate.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧪 R20 Merge Gate"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

"$PYTHON_BIN" scripts/r20_merge_gate.py "$@"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Merge gate пройден."
else
  echo "❌ Merge gate не пройден (есть обязательные ошибки)."
fi
echo "Отчеты: artifacts/ops/r20_merge_gate_latest.json"
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
