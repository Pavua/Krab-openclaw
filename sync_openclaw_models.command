#!/bin/bash
# Синхронизация OpenClaw models.json из .env одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/sync_openclaw_models.py
SYNC_CODE=$?

if [ "$SYNC_CODE" -eq 0 ]; then
  echo ""
  echo "Применяю openclaw secrets reload..."
  openclaw secrets reload || true
fi

echo ""
echo "Готово. Код выхода: $SYNC_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $SYNC_CODE
