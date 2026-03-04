#!/bin/bash
# One-click pre-release smoke для Krab.

set -euo pipefail
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

EXTRA_ARGS=()
if [ "${1:-}" = "full" ]; then
  EXTRA_ARGS+=("--full")
fi
if [ "${2:-}" = "strict" ] || [ "${1:-}" = "strict" ]; then
  EXTRA_ARGS+=("--strict-runtime")
fi

python3 scripts/pre_release_smoke.py "${EXTRA_ARGS[@]}"
EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
