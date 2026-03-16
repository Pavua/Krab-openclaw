#!/bin/zsh
# Очищает runtime chat-session через owner web API одним кликом.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ $# -lt 1 ]; then
  echo "Использование:"
  echo "  ./Clear Runtime Chat Session.command <chat_id> [note]"
  echo
  echo "Пример:"
  echo "  ./Clear Runtime Chat Session.command 312322764 'flush amnesia tail'"
  echo
  echo "Нажми Enter для закрытия окна..."
  read -r _
  exit 2
fi

CHAT_ID="$1"
NOTE="${2:-}"

echo "🧹 Очищаю runtime chat-session..."
echo "👤 Пользователь: $(whoami)"
echo "💬 chat_id: $CHAT_ID"
echo

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif [ -x "venv/bin/python" ]; then
  PYTHON_BIN="venv/bin/python"
else
  PYTHON_BIN="python3"
fi

set +e
"$PYTHON_BIN" scripts/clear_runtime_chat_session.py --chat-id "$CHAT_ID" --note "$NOTE"
EXIT_CODE=$?
set -e

echo ""
echo "Готово. Код выхода: $EXIT_CODE"
echo "Нажми Enter для закрытия окна..."
read -r _
exit $EXIT_CODE
