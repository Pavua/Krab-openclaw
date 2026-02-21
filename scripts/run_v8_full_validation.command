#!/bin/bash
# Полный цикл проверки Krab v8 одним кликом (macOS .command).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🦀 Krab v8 Full Validation"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo

echo "1/3 pytest -q"
"$PYTHON_BIN" -m pytest -q
echo

echo "2/3 smoke_test.py"
"$PYTHON_BIN" tests/smoke_test.py
echo

echo "3/3 merge_guard --full"
"$PYTHON_BIN" scripts/merge_guard.py --full
echo

echo "✅ Валидация завершена успешно."
read -p "Нажми Enter, чтобы закрыть окно..."
