#!/bin/bash
# One-click repair/login для Codex CLI.
# Не трогаем OpenClaw OAuth: это отдельный локальный CLI-контур с собственной сессией.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Codex CLI Login ==="
echo ""

if ! command -v codex >/dev/null 2>&1; then
  echo "❌ Команда 'codex' не найдена в PATH."
  echo "Установи Codex CLI или открой этот helper из той учётки, где он уже доступен."
  echo ""
  read -p "Нажми Enter для закрытия окна..."
  exit 127
fi

echo "Текущий статус:"
codex login status || true
echo ""
echo "Запускаю device-auth login для Codex CLI..."
codex login --device-auth
LOGIN_CODE=$?

echo ""
if [ "$LOGIN_CODE" -eq 0 ]; then
  echo "✅ Codex CLI login завершён успешно."
  echo ""
  echo "Обновлённый статус:"
  codex login status || true
else
  echo "❌ Codex CLI login завершился с кодом: $LOGIN_CODE"
fi

echo ""
read -p "Нажми Enter для закрытия окна..."
exit $LOGIN_CODE
