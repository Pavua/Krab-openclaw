#!/bin/zsh
# Останавливает runtime текущей учётки и собирает fresh switchover/handoff артефакты.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

exec "$PYTHON_BIN" scripts/runtime_switch_assistant.py freeze-current
