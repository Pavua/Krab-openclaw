#!/bin/zsh
# -----------------------------------------------------------------------------
# One-click pre-release smoke для Krab.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

"$PY" scripts/pre_release_smoke.py "$@"
