#!/bin/bash
# One-click smoke для каналов OpenClaw и критичных паттернов стабильности.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

OUT_DIR="artifacts/live_smoke"
mkdir -p "$OUT_DIR"
STAMP="$(date +"%Y%m%d_%H%M%S")"
OUT_FILE="$OUT_DIR/live_channel_smoke_${STAMP}.json"

python3 scripts/live_channel_smoke.py --output "$OUT_FILE"
SMOKE_CODE=$?

echo ""
echo "Отчёт: $OUT_FILE"
echo "Готово. Код выхода: $SMOKE_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $SMOKE_CODE
