#!/bin/bash
# One-click аудит каналов/скиллов OpenClaw для Krab.

set -euo pipefail
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

EXTRA_ARGS=()
if [ "${1:-}" = "apply" ]; then
  EXTRA_ARGS+=("--apply-safe")
  shift || true
fi
if [ -n "${1:-}" ]; then
  EXTRA_ARGS+=("--enable" "$1")
fi

python3 scripts/openclaw_channels_skills_bootstrap.py "${EXTRA_ARGS[@]}"
EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
