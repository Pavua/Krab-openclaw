#!/bin/bash
# One-click repair/login для Google Antigravity OAuth через OpenClaw.
# ВАЖНО: google-antigravity — legacy провайдер. Предпочитай google-gemini-cli.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Google Antigravity OAuth Login ==="
echo "⚠️  Это legacy провайдер. Рекомендуется использовать 'Login Gemini CLI OAuth.command' вместо этого."
echo ""

echo "Включаю plugin google-antigravity-auth..."
openclaw plugins enable google-antigravity-auth 2>/dev/null || echo "(plugin уже включён или не требуется)"

echo ""
echo "Запускаю browser login для Google Antigravity..."
openclaw models auth login --provider google-antigravity --set-default
LOGIN_CODE=$?

echo ""
if [ "$LOGIN_CODE" -eq 0 ]; then
  echo "✅ Авторизация Google Antigravity прошла успешно."
  openclaw models status | grep -A2 "google-antigravity"
else
  echo "❌ Авторизация завершилась с кодом: $LOGIN_CODE"
fi

echo ""
read -p "Нажми Enter для закрытия окна..."
exit $LOGIN_CODE
