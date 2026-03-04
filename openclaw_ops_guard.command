#!/bin/bash
# One-click OpenClaw ops guard.

set -euo pipefail
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

EXTRA_ARGS=()
if [ "${1:-}" = "fix" ]; then
  EXTRA_ARGS+=("--fix")
fi
if [ -n "${2:-}" ]; then
  EXTRA_ARGS+=("--profile" "$2")
fi

python3 scripts/openclaw_ops_guard.py "${EXTRA_ARGS[@]}"
EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
