#!/bin/bash
# One-click repair/login для Gemini CLI OAuth через текущий OpenClaw runtime.
# Сначала пробуем безопасный sync из уже установленного `gemini` CLI, и только
# если этого недостаточно — уходим в официальный browser-based login OpenClaw.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "Включаю bundled plugin google-gemini-cli-auth..."
openclaw plugins enable google-gemini-cli-auth
ENABLE_CODE=$?

if [ "$ENABLE_CODE" -ne 0 ]; then
  echo ""
  echo "Не удалось включить plugin. Код выхода: $ENABLE_CODE"
  read -p "Нажми Enter для закрытия окна..."
  exit $ENABLE_CODE
fi

echo ""
if [ -f "$HOME/.gemini/oauth_creds.json" ]; then
  echo "Найден существующий Gemini CLI OAuth store. Пробую безопасную синхронизацию..."
  python3 "$DIR/scripts/sync_gemini_cli_oauth.py"
  SYNC_CODE=$?

  if [ "$SYNC_CODE" -eq 0 ]; then
    echo ""
    echo "Синхронизация завершилась успешно."
    echo "Показываю актуальный статус моделей..."
    openclaw models status
    FINAL_CODE=$?
    echo ""
    echo "Готово. Код выхода: $FINAL_CODE"
    read -p "Нажми Enter для закрытия окна..."
    exit $FINAL_CODE
  fi

  echo ""
  echo "Синхронизация не удалась. Перехожу к официальному browser login..."
  echo ""
fi

openclaw models auth login --provider google-gemini-cli --set-default
LOGIN_CODE=$?

echo ""
echo "Готово. Код выхода: $LOGIN_CODE"
read -p "Нажми Enter для закрытия окна..."
exit $LOGIN_CODE
