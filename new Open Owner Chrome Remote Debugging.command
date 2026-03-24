#!/bin/zsh
# Запускает Chrome с remote debugging для Krab Browser MCP.
#
# Решение Chrome 146 policy block:
# - Chrome 146+ блокирует CDP на дефолтном профиле (~/Library/Application Support/Google/Chrome)
# - Достаточно указать любой другой --user-data-dir — блокировки нет
# - Используем постоянный профиль ~/.openclaw/chrome-debug-profile
# - Первый запуск: пустой профиль, нужно войти в Google один раз
# - Последующие запуски: профиль сохраняется (куки, расширения, история)
#
# При этом обычный Chrome остаётся нетронутым и может работать параллельно.

set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app"
CHROME_BIN="${CHROME_APP}/Contents/MacOS/Google Chrome"
REMOTE_DEBUGGING_PORT="9222"
KRAB_CHROME_PROFILE="${HOME}/.openclaw/chrome-debug-profile"
TARGET_URL="about:blank"
LOG_PATH="/tmp/krab-owner-chrome-remote-debugging.log"

is_krab_chrome_running() {
  pgrep -f "chrome-debug-profile.*remote-debugging-port" >/dev/null 2>&1 || \
  pgrep -f "remote-debugging-port=${REMOTE_DEBUGGING_PORT}" >/dev/null 2>&1
}

if [ ! -x "$CHROME_BIN" ]; then
  echo "Google Chrome не найден: $CHROME_BIN"
  exit 1
fi

mkdir -p "$KRAB_CHROME_PROFILE"

echo "============================================"
echo " Krab Chrome Debug Profile"
echo " Профиль: $KRAB_CHROME_PROFILE"
echo " Порт CDP: $REMOTE_DEBUGGING_PORT"
echo " Лог: $LOG_PATH"
echo "============================================"

# Если уже запущен debug-инстанс — завершаем его чисто
if is_krab_chrome_running; then
  echo "Обнаружен работающий debug Chrome, перезапускаю..."
  pkill -f "remote-debugging-port=${REMOTE_DEBUGGING_PORT}" >/dev/null 2>&1 || true
  sleep 2
fi

rm -f "$LOG_PATH"

echo "Запускаю Chrome с non-default profile и --remote-debugging-port=${REMOTE_DEBUGGING_PORT}..."
"$CHROME_BIN" \
  --remote-debugging-port="$REMOTE_DEBUGGING_PORT" \
  --user-data-dir="$KRAB_CHROME_PROFILE" \
  --no-first-run \
  --no-default-browser-check \
  --disable-features=TranslateUI \
  "$TARGET_URL" >"$LOG_PATH" 2>&1 &

CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# Ждём пока порт не поднимется (до 10 сек)
for i in $(seq 1 10); do
  sleep 1
  if curl -sf "http://127.0.0.1:${REMOTE_DEBUGGING_PORT}/json/version" >/dev/null 2>&1; then
    echo ""
    echo "✅ CDP доступен: http://127.0.0.1:${REMOTE_DEBUGGING_PORT}/json/version"
    echo ""
    echo "Если это первый запуск — войди в Google в открывшемся Chrome."
    echo "Профиль сохранится в: $KRAB_CHROME_PROFILE"
    echo ""
    echo "Теперь обнови Browser/MCP Readiness в панели :8080"
    exit 0
  fi

  # Проверяем policy block (на случай если старый путь вернулся)
  if grep -qi "non-default data directory" "$LOG_PATH" 2>/dev/null; then
    echo ""
    echo "❌ Chrome снова отклонил remote debugging."
    echo "Лог: $LOG_PATH"
    exit 3
  fi
done

# Если порт так и не поднялся — смотрим DevToolsActivePort как fallback
if [ -f "$KRAB_CHROME_PROFILE/DevToolsActivePort" ]; then
  PORT_LINE="$(head -1 "$KRAB_CHROME_PROFILE/DevToolsActivePort" 2>/dev/null || true)"
  echo ""
  echo "⚠️  /json/version не ответил, но DevToolsActivePort говорит порт: $PORT_LINE"
  echo "Попробуй: curl http://127.0.0.1:${PORT_LINE}/json/version"
  exit 0
fi

echo ""
echo "⚠️  CDP не ответил за 10 секунд. Лог: $LOG_PATH"
echo "Возможно Chrome ещё стартует — подожди и повтори:"
echo "  curl http://127.0.0.1:${REMOTE_DEBUGGING_PORT}/json/version"
exit 0
