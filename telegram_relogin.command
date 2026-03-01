#!/bin/bash
# Интерактивный relogin Telegram для Krab одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "venv" ]; then
  source venv/bin/activate
fi

python3 scripts/telegram_relogin.py
EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
