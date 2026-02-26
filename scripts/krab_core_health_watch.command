#!/bin/bash
# One-click монитор стабильности HTTP health Krab Core.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🩺 Krab Core Health Watch"
echo "📂 Root: $ROOT_DIR"
echo "🐍 Python: $PYTHON_BIN"
echo
echo "Параметры по умолчанию: --duration-sec 120 --interval-sec 2 --probe-timeout-sec 4 --url http://127.0.0.1:8080/api/health/lite"
echo

"$PYTHON_BIN" scripts/krab_core_health_watch.py "$@"
EXIT_CODE=$?

echo
if [ "$EXIT_CODE" -eq 0 ]; then
  echo "✅ Health watch завершён: HTTP health стабилен."
else
  echo "❌ Health watch: обнаружены падения/fлапы."
fi
echo "Отчёты: artifacts/ops/krab_core_health_watch_latest.json"
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
