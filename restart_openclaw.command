#!/bin/bash

###############################################################################
# restart_openclaw.command
# Безопасный перезапуск PROD OpenClaw Gateway (порт 18789) через launchd.
# Зачем: избегаем дублей процессов и конфликтов порта ("already in use"),
# а также не трогаем LAB-инстанс на 18890.
###############################################################################

set -euo pipefail

UID_LOCAL="$(id -u)"
LABEL="ai.openclaw.gateway"
PLIST_PATH="${HOME}/Library/LaunchAgents/${LABEL}.plist"
PORT="18789"

echo "⏹ Останавливаю ${LABEL} (если запущен)..."
launchctl bootout "gui/${UID_LOCAL}/${LABEL}" >/dev/null 2>&1 || true

echo "🧹 Проверяю сиротские процессы на порту ${PORT}..."
for pid in $(lsof -ti "tcp:${PORT}" 2>/dev/null || true); do
  kill "${pid}" >/dev/null 2>&1 || true
done

sleep 1

if [ ! -f "${PLIST_PATH}" ]; then
  echo "❌ Не найден LaunchAgent plist: ${PLIST_PATH}"
  echo "Подсказка: установи сервис командой: openclaw gateway install"
  exit 1
fi

echo "▶️ Поднимаю ${LABEL} через launchd..."
launchctl bootstrap "gui/${UID_LOCAL}" "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/${UID_LOCAL}/${LABEL}"

echo "⏳ Жду, пока сервис поднимет порт ${PORT}..."
READY=0
for _ in {1..20}; do
  if lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

echo "🔎 Проверяю слушатель порта ${PORT}..."
if [ "${READY}" -eq 1 ]; then
  lsof -nP -iTCP:"${PORT}" -sTCP:LISTEN
  echo "✅ PROD OpenClaw Gateway успешно перезапущен."
else
  echo "⚠️ После перезапуска порт ${PORT} не слушается."
  echo "Проверь: launchctl print gui/${UID_LOCAL}/${LABEL}"
  exit 2
fi
