#!/bin/bash
# -----------------------------------------------------------------------------
# One-click аудит macOS permission/Gatekeeper readiness для Краба.
# Связи: вызывает `scripts/check_macos_permissions.py` и не дублирует логику в shell.
# -----------------------------------------------------------------------------

set -euo pipefail

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -x "$DIR/venv/bin/python" ]; then
  PYTHON_BIN="$DIR/venv/bin/python"
elif [ -x "$DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$DIR/.venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || true)"
fi

if [ -z "${PYTHON_BIN:-}" ]; then
  echo "❌ python3 не найден."
  exit 1
fi

echo "🔍 Запускаю macOS Permission Audit..."
"$PYTHON_BIN" "$DIR/scripts/check_macos_permissions.py" --write-artifact
