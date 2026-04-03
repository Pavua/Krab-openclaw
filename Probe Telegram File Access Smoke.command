#!/bin/zsh
# -*- coding: utf-8 -*-

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
SMOKE_SCRIPT="$PROJECT_ROOT/scripts/probe_telegram_file_access_smoke.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "❌ Не найден Python venv: $PYTHON_BIN"
  echo "Нажми Enter для закрытия окна..."
  read
  exit 1
fi

if [[ ! -f "$SMOKE_SCRIPT" ]]; then
  echo "❌ Не найден smoke-скрипт: $SMOKE_SCRIPT"
  echo "Нажми Enter для закрытия окна..."
  read
  exit 1
fi

cd "$PROJECT_ROOT" || exit 1

echo "🧪 Запускаю Telegram file-access smoke для !probe..."
echo "📂 Project: $PROJECT_ROOT"
"$PYTHON_BIN" "$SMOKE_SCRIPT" "$@"
STATUS=$?
echo ""
if [[ $STATUS -eq 0 ]]; then
  echo "✅ Smoke завершён успешно."
  echo "📄 Артефакт: artifacts/ops/probe_telegram_file_access_smoke_latest.json"
else
  echo "❌ Smoke завершился с ошибкой (exit $STATUS)."
  echo "📄 Последний артефакт: artifacts/ops/probe_telegram_file_access_smoke_latest.json"
fi
echo "Нажми Enter для закрытия окна..."
read
exit $STATUS
