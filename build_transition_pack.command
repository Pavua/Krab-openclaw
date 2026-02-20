#!/bin/zsh
# -----------------------------------------------------------------------------
# Сборка anti-413 transition-пака одним кликом для переноса в новый диалог.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

"$PY" scripts/build_transition_pack.py
