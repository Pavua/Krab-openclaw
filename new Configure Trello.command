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
openclaw config set env.TRELLO_API_KEY "$TRELLO_API_KEY"
openclaw config set env.TRELLO_TOKEN "$TRELLO_TOKEN"

echo
echo "🔄 Применяю секреты/конфиг в runtime..."
if openclaw health >/dev/null 2>&1; then
  # Если gateway уже запущен, применяем новые секреты сразу в текущий runtime.
  if ! openclaw secrets reload >/dev/null 2>&1; then
    echo "⚠️ Не удалось сделать hot-reload секретов. Они всё равно применятся при следующем запуске gateway."
  else
    echo "✅ Секреты применены в активном runtime."
  fi
else
  # Нормальная ситуация для foreground-запуска через локальный скрипт.
  echo "ℹ️ Gateway сейчас недоступен по ws://127.0.0.1:18789; секреты уже сохранены в конфиг и применятся при следующем старте."
fi

echo
echo "🩺 Проверяю статус skills..."
openclaw skills check

echo
echo "✅ Настройка Trello завершена."
