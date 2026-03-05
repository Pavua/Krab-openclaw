#!/bin/bash
# Быстрая и безопасная привязка BlueBubbles к OpenClaw.
# Скрипт запрашивает пароль скрытым вводом и не печатает его в лог.

set -euo pipefail

PROJECT_DIR="/Users/pablito/Antigravity_AGENTS/Краб"
DEFAULT_URL="http://127.0.0.1:12345"
DEFAULT_WEBHOOK_PATH="/bluebubbles-webhook"

cd "$PROJECT_DIR"

echo "🫧 Настройка BlueBubbles для OpenClaw"
echo "📂 Проект: $PROJECT_DIR"
echo
echo "Подсказка: если значения уже подходят, просто нажимай Enter для значений по умолчанию."
echo

read -rp "Server URL [$DEFAULT_URL]: " BLUEBUBBLES_URL
BLUEBUBBLES_URL="${BLUEBUBBLES_URL:-$DEFAULT_URL}"

read -rp "Webhook path [$DEFAULT_WEBHOOK_PATH]: " BLUEBUBBLES_WEBHOOK_PATH
BLUEBUBBLES_WEBHOOK_PATH="${BLUEBUBBLES_WEBHOOK_PATH:-$DEFAULT_WEBHOOK_PATH}"

read -rsp "Пароль BlueBubbles (ввод скрыт): " BLUEBUBBLES_PASSWORD
echo

if [[ -z "$BLUEBUBBLES_PASSWORD" ]]; then
  echo "❌ Пароль пустой, настройка остановлена."
  exit 1
fi

echo "🔧 Применяю конфигурацию канала..."
openclaw channels add \
  --channel bluebubbles \
  --http-url "$BLUEBUBBLES_URL" \
  --password "$BLUEBUBBLES_PASSWORD" \
  --webhook-path "$BLUEBUBBLES_WEBHOOK_PATH"

echo
echo "🔄 Перезапускаю gateway..."
openclaw gateway restart

echo
echo "🩺 Проверяю статус каналов..."
openclaw channels status --probe

echo
echo "✅ BlueBubbles конфигурация применена."
