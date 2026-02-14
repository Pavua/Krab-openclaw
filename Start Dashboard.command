#!/bin/zsh
# Быстрое открытие Krab Web Panel в браузере.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# Читаем .env если есть
if [ -f ".env" ]; then
  set -a
  source ./.env
  set +a
fi

PANEL_URL="${WEB_PUBLIC_BASE_URL:-}"
if [ -z "$PANEL_URL" ]; then
  PANEL_HOST="${WEB_HOST:-127.0.0.1}"
  PANEL_PORT="${WEB_PORT:-8080}"
  PANEL_URL="http://${PANEL_HOST}:${PANEL_PORT}"
fi

echo "🕸️ Проверяю доступность Krab Web Panel: ${PANEL_URL}"
need_start=0
if command -v curl >/dev/null 2>&1; then
  if curl -sS --max-time 2 "${PANEL_URL}/api/health" >/dev/null 2>&1; then
    echo "✅ Панель уже доступна."
  else
    echo "⚠️ Панель недоступна. Запускаю Krab..."
    need_start=1
  fi
else
  echo "⚠️ curl не найден, запускаем Krab на всякий случай."
  need_start=1
fi

if [ $need_start -eq 1 ]; then
  osascript <<EOF
    tell application "Terminal"
      activate
      do script "cd '${ROOT_DIR}' && ./run_krab.sh"
    end tell
EOF
  echo "🚀 Жду, пока Krab поднимется..."
  if command -v curl >/dev/null 2>&1; then
    attempts=0
    until curl -sS --max-time 2 "${PANEL_URL}/api/health" >/dev/null 2>&1 || [ $attempts -ge 12 ]; do
      attempts=$((attempts + 1))
      sleep 2
    done
    if [ $attempts -ge 12 ]; then
      echo "⚠️ Krab всё ещё недоступен — проверь логи (krab.log)"
    else
      echo "✅ Krab и панель доступны."
    fi
  fi
fi

open "$PANEL_URL"
