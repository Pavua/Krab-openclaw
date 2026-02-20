#!/bin/zsh
# -----------------------------------------------------------------------------
# One-click autoswitch default model в OpenClaw:
# LM loaded -> local, LM unloaded -> cloud.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

# Подхватываем runtime-переменные из .env (включая LM_STUDIO_URL).
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
else
  PYTHON_BIN="$(command -v python3 || command -v python)"
fi

echo "🔁 OpenClaw model autoswitch (single pass)"
"$PYTHON_BIN" "$ROOT_DIR/scripts/openclaw_model_autoswitch.py"
echo
echo "✅ Готово."
