#!/bin/bash
# Проверка voice event schema v1.0 (macOS one-click).
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

SAMPLE='{"type":"stt.partial","data":{"session_id":"vs_demo","latency_ms":120,"source":"twilio_media"}}'

echo "🧪 Voice Event Schema Check"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo
echo "Sample event:"
echo "$SAMPLE"
echo

"$PYTHON_BIN" scripts/check_voice_event_schema.py "$SAMPLE"

echo
echo "✅ Проверка завершена."
read -p "Нажми Enter, чтобы закрыть окно..."
