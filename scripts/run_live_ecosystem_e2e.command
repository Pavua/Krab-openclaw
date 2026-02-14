#!/bin/bash
# Live E2E проверка 3-проектной экосистемы одним кликом (macOS .command).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧪 Live Ecosystem E2E"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo
echo "Проверка: OpenClaw + Local LM + Voice Gateway + Krab Ear"
echo "Voice lifecycle: create -> patch -> diagnostics -> stop"
echo

"$PYTHON_BIN" scripts/live_ecosystem_e2e.py

echo
echo "✅ Live E2E завершен."
read -p "Нажми Enter, чтобы закрыть окно..."
