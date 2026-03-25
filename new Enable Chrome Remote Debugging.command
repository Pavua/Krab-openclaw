#!/bin/zsh
# Запускает Google Chrome с Remote Debugging Port для подключения Краба через CDP.
# Использует отдельный user-data-dir, чтобы не мешать основному профилю.
#
# Порт: переменная KRAB_CDP_PORT (default 9222).
# Пример: KRAB_CDP_PORT=9223 ./new\ Enable\ Chrome\ Remote\ Debugging.command

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_PORT="${KRAB_CDP_PORT:-9222}"

if [ ! -x "$CHROME" ]; then
    echo "❌ Google Chrome не найден по пути: $CHROME"
    echo "Установи Chrome или обнови путь в этом скрипте."
    read -r "?Нажмите Enter для закрытия..."
    exit 1
fi

echo "🌐 Запускаю Chrome с remote debugging port ${CDP_PORT}..."
echo "📂 User data dir: $DIR/browser_data"

exec "$CHROME" \
    --remote-debugging-port="${CDP_PORT}" \
    --user-data-dir="$DIR/browser_data" \
    --no-first-run \
    --no-default-browser-check \
    "about:blank"
