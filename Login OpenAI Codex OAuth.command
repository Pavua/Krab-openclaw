#!/bin/bash
# One-click repair/login для OpenAI Codex OAuth через OpenClaw.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== OpenAI Codex OAuth Login ==="
echo ""
echo "Запускаю browser login для OpenAI Codex..."
openclaw models auth login --provider openai-codex --set-default
LOGIN_CODE=$?

echo ""
if [ "$LOGIN_CODE" -eq 0 ]; then
  echo "✅ Авторизация OpenAI Codex прошла успешно."
  echo ""
  echo "Показываю актуальный статус..."
  openclaw models status | grep -A2 "openai-codex"
else
  echo "❌ Авторизация завершилась с кодом: $LOGIN_CODE"
fi

echo ""
read -p "Нажми Enter для закрытия окна..."
exit $LOGIN_CODE
