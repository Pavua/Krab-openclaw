#!/bin/bash
# Krab Quick Input — глобальный hotkey ввод
# Показывает dialog macOS, отправляет текст Крабу через /api/notify

KRAB_API="http://127.0.0.1:8080/api/notify"
CHAT_ID="${KRAB_INBOX_TARGET:-@p0lrd}"

# AppleScript dialog для ввода текста
USER_TEXT=$(osascript -e '
tell application "System Events"
    set frontApp to name of first application process whose frontmost is true
end tell
set userInput to text returned of (display dialog "Сообщение для Краба:" default answer "" with title "🦀 Краб" buttons {"Отмена", "Отправить"} default button "Отправить" cancel button "Отмена")
return userInput
' 2>/dev/null)

# Если нажали Отмена или пустой ввод — выходим
[ -z "$USER_TEXT" ] && exit 0

# Отправляем через API
PAYLOAD=$(python3 -c "
import json, sys
text = sys.argv[1]
print(json.dumps({'text': '💬 ' + text, 'chat_id': '$CHAT_ID'}))
" "$USER_TEXT")

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "$KRAB_API" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

if [ "$HTTP_STATUS" = "200" ]; then
    # Показываем подтверждение
    osascript -e "display notification \"Отправлено: $USER_TEXT\" with title \"🦀 Краб\" sound name \"Tink\""
else
    osascript -e "display notification \"Ошибка ($HTTP_STATUS): Краб недоступен\" with title \"🦀 Краб\""
fi
