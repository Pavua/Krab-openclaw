#!/bin/bash
# Назначение: one-click вход в релизный merge-gate для крупных этапов Krab/OpenClaw.
# Связь с проектом: запускает канонический pre-release smoke в строгом режиме и пишет
# отчёты в artifacts/ops, чтобы перед commit/push/PR был один воспроизводимый checkpoint.

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif [ -x ".venv_krab/bin/python" ]; then
  PYTHON_BIN=".venv_krab/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🚦 Krab Release Gate"
echo "📂 Root: $DIR"
echo "🐍 Python: $PYTHON_BIN"
echo "🧪 Режим: pre-release smoke --full --strict-runtime"
echo

if "$PYTHON_BIN" scripts/pre_release_smoke.py --full --strict-runtime; then
  EXIT_CODE=0
else
  EXIT_CODE=$?
fi

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Release gate пройден."
else
  echo "❌ Release gate не пройден."
fi
echo "Отчёты:"
echo "  - artifacts/ops/pre_release_smoke_latest.json"
echo "  - artifacts/ops/r20_merge_gate_latest.json"
echo "Документация:"
echo "  - docs/RELEASE_CHECKLIST_RU.md"
echo
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
