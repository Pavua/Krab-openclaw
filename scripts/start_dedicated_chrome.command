#!/bin/bash
# start_dedicated_chrome.command — запускает Chrome в debug mode для CDP (порт 9222)
# Используется как fallback при "!screenshot CDP error"
# Можно запускать двойным кликом из Finder или из терминала

set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_BETA="/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta"
CHROMIUM="/Applications/Chromium.app/Contents/MacOS/Chromium"

CDP_PORT="${DEDICATED_CHROME_PORT:-9222}"
PROFILE_DIR="${DEDICATED_CHROME_PROFILE_DIR:-/tmp/krab-chrome}"

# Найти Chrome binary
CHROME_BIN=""
for candidate in "$CHROME_APP" "$CHROME_BETA" "$CHROMIUM"; do
    if [ -f "$candidate" ]; then
        CHROME_BIN="$candidate"
        break
    fi
done

if [ -z "$CHROME_BIN" ]; then
    echo "❌ Chrome не найден. Установи Google Chrome."
    exit 1
fi

# Проверить, уже ли запущен
if curl -sf "http://127.0.0.1:${CDP_PORT}/json/version" > /dev/null 2>&1; then
    echo "✅ Chrome уже запущен на порту ${CDP_PORT}"
    exit 0
fi

mkdir -p "$PROFILE_DIR"

echo "🚀 Запускаю Chrome с CDP на порту ${CDP_PORT}..."
echo "   Profile: ${PROFILE_DIR}"

"$CHROME_BIN" \
    "--user-data-dir=${PROFILE_DIR}" \
    "--remote-debugging-port=${CDP_PORT}" \
    "--no-first-run" \
    "--no-default-browser-check" \
    "--disable-default-apps" \
    "--disable-popup-blocking" \
    "--disable-prompt-on-repost" \
    "--no-crash-upload" \
    "--disable-features=TranslateUI" \
    "about:blank" \
    > /dev/null 2>&1 &

# Ждём готовности (до 10 секунд)
for i in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${CDP_PORT}/json/version" > /dev/null 2>&1; then
        echo "✅ Chrome готов — CDP на порту ${CDP_PORT}"
        exit 0
    fi
    sleep 0.5
done

echo "⚠️  Chrome запущен, но CDP порт ещё не отвечает. Подожди несколько секунд и повтори !screenshot."
exit 1
