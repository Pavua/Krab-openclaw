#!/bin/bash
# Быстрая настройка Trello-ключей для OpenClaw.
# Скрипт безопасно запрашивает секреты и сразу проверяет статус skills.

set -euo pipefail

PROJECT_DIR="/Users/pablito/Antigravity_AGENTS/Краб"

cd "$PROJECT_DIR"

echo "📋 Настройка Trello для OpenClaw"
echo "📂 Проект: $PROJECT_DIR"
echo
echo "Нужны ДВА значения именно из Trello:"
echo "1) TRELLO_API_KEY (https://trello.com/app-key)"
echo "2) TRELLO_TOKEN   (ссылка Token на той же странице)"
echo

read -rsp "TRELLO_API_KEY (ввод скрыт): " TRELLO_API_KEY
echo
read -rsp "TRELLO_TOKEN (ввод скрыт): " TRELLO_TOKEN
echo

if [[ -z "$TRELLO_API_KEY" || -z "$TRELLO_TOKEN" ]]; then
  echo "❌ API Key или Token пустой. Настройка остановлена."
  exit 1
fi

echo "🔧 Записываю переменные в OpenClaw..."
openclaw config env set TRELLO_API_KEY "$TRELLO_API_KEY"
openclaw config env set TRELLO_TOKEN "$TRELLO_TOKEN"

echo
echo "🔄 Обновляю gateway (если gateway запущен как foreground-процесс, команда может сообщить, что service не загружен — это не критично)."
openclaw gateway restart || true

echo
echo "🩺 Проверяю статус skills..."
openclaw skills check

echo
echo "✅ Настройка Trello завершена."
