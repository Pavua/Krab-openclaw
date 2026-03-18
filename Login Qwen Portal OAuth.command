#!/bin/bash
# One-click repair/login для Qwen Portal OAuth через OpenClaw.
# Открывает browser-based login flow через OpenClaw plugin.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Qwen Portal OAuth Login ==="
echo ""
echo "Включаю plugin qwen-portal-auth..."
openclaw plugins enable qwen-portal-auth 2>/dev/null || echo "(plugin уже включён или не требуется)"

echo ""
echo "Запускаю browser login для Qwen Portal..."
openclaw models auth login --provider qwen-portal --set-default
LOGIN_CODE=$?

echo ""
if [ "$LOGIN_CODE" -eq 0 ]; then
  echo "✅ Авторизация Qwen Portal прошла успешно."
  echo ""
  echo "Показываю актуальный статус модели..."
  openclaw models status | grep -A2 "qwen-portal"
else
  echo "❌ Авторизация завершилась с кодом: $LOGIN_CODE"
  echo "Проверьте интернет-соединение и попробуйте снова."
fi

echo ""
read -p "Нажми Enter для закрытия окна..."
exit $LOGIN_CODE
