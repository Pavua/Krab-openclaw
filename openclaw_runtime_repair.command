#!/bin/bash
# Восстановление runtime OpenClaw/каналов одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/openclaw_runtime_repair.py
REPAIR_CODE=$?

if [ "$REPAIR_CODE" -eq 0 ]; then
  echo ""
  echo "Применяю openclaw secrets reload..."
  openclaw secrets reload || true
fi

echo ""
echo "Готово. Код выхода: $REPAIR_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $REPAIR_CODE
