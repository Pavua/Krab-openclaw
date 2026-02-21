#!/bin/zsh
# -----------------------------------------------------------------------------
# Обертка для one-click autoswitch OpenClaw модели.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT_DIR/scripts/openclaw_model_autoswitch.command" "$@"
