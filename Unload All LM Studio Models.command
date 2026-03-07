#!/bin/bash
# One-click выгрузка всех загруженных моделей LM Studio.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🧊 Unload All LM Studio Models"
echo "📂 Root: $ROOT_DIR"
echo
"$PYTHON_BIN" scripts/lmstudio_control.py unload-all "$@"
EXIT_CODE=$?
echo
read -p "Нажми Enter, чтобы закрыть окно..."
exit "$EXIT_CODE"
