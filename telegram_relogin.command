#!/bin/bash
# Интерактивный relogin Telegram для Krab одним кликом.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PY_BIN=""
if [ -d ".venv" ]; then
  source .venv/bin/activate
  PY_BIN="$DIR/.venv/bin/python3"
elif [ -d "venv" ]; then
  source venv/bin/activate
  PY_BIN="$DIR/venv/bin/python3"
fi

if [ -z "$PY_BIN" ]; then
  PY_BIN="$(command -v python3)"
fi

"$PY_BIN" scripts/telegram_relogin.py
EXIT_CODE=$?

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $EXIT_CODE
