#!/bin/bash
# 🦀 OpenClaw Max Setup (macOS one-click)
# Назначение: довести до "почти всё ready" без ручного ковыряния CLI.
# Связь: использует openclaw CLI и локальный конфиг ~/.openclaw/openclaw.json.

set -euo pipefail

echo "🦀 OpenClaw Max Setup"
echo "📍 Этот мастер настроит: tmux, spotify-player (spogo), voice-call, Trello env, BlueBubbles."
echo

OPENCLAW_BIN="$(command -v openclaw || true)"
if [ -z "${OPENCLAW_BIN}" ]; then
  echo "❌ openclaw CLI не найден в PATH."
  read -r -p "Нажми Enter для выхода..."
  exit 1
fi

BREW_BIN="$(command -v brew || true)"
if [ -z "${BREW_BIN}" ]; then
  echo "❌ Homebrew не найден. Установи Homebrew и запусти снова."
  read -r -p "Нажми Enter для выхода..."
  exit 1
fi

echo "1) Проверка системных зависимостей..."
if ! command -v tmux >/dev/null 2>&1; then
  echo "• Устанавливаю tmux..."
  "${BREW_BIN}" install tmux
else
  echo "• tmux уже установлен."
fi

if ! command -v spogo >/dev/null 2>&1; then
  echo "• Устанавливаю spogo (Spotify skill backend)..."
  "${BREW_BIN}" install spogo
else
  echo "• spogo уже установлен."
fi

echo
echo "2) Включение voice-call plugin..."
"${OPENCLAW_BIN}" plugins enable voice-call >/dev/null 2>&1 || true
echo "• voice-call включен (если был включен ранее — изменений нет)."

echo
echo "3) Trello (опционально)"
echo "Если введёшь ключи сейчас — skill trello станет ready."
read -r -p "• Ввести Trello ключи сейчас? [y/N]: " TRELLO_NOW
if [[ "${TRELLO_NOW}" =~ ^[Yy]$ ]]; then
  read -r -p "  TRELLO_API_KEY: " TRELLO_API_KEY
  read -r -s -p "  TRELLO_TOKEN (скрыт): " TRELLO_TOKEN
  echo
  if [ -n "${TRELLO_API_KEY}" ] && [ -n "${TRELLO_TOKEN}" ]; then
    "${OPENCLAW_BIN}" config set env.TRELLO_API_KEY "${TRELLO_API_KEY}" >/dev/null
    "${OPENCLAW_BIN}" config set env.TRELLO_TOKEN "${TRELLO_TOKEN}" >/dev/null
    echo "• Trello ключи сохранены в openclaw config env."
  else
    echo "⚠️ Пропущено: один из Trello параметров пуст."
  fi
else
  echo "• Trello пропущен."
fi

echo
echo "4) BlueBubbles (опционально)"
echo "Важно: LM Studio уже занимает порт 1234, поэтому для BlueBubbles лучше использовать 12345."
read -r -p "• Настроить BlueBubbles сейчас? [y/N]: " BB_NOW
if [[ "${BB_NOW}" =~ ^[Yy]$ ]]; then
  read -r -p "  BlueBubbles URL [http://127.0.0.1:12345]: " BB_URL
  BB_URL="${BB_URL:-http://127.0.0.1:12345}"
  read -r -s -p "  BlueBubbles password (скрыт): " BB_PASS
  echo
  read -r -p "  Webhook path [/bluebubbles-webhook]: " BB_WEBHOOK
  BB_WEBHOOK="${BB_WEBHOOK:-/bluebubbles-webhook}"

  if [ -n "${BB_PASS}" ]; then
    "${OPENCLAW_BIN}" channels add \
      --channel bluebubbles \
      --http-url "${BB_URL}" \
      --password "${BB_PASS}" \
      --webhook-path "${BB_WEBHOOK}"
    echo "• BlueBubbles канал записан в конфиг."
    echo "• Проверяю ping endpoint через OpenClaw..."
  else
    echo "⚠️ Пропущено: пустой BlueBubbles password."
  fi
else
  echo "• BlueBubbles пропущен."
fi

echo
echo "5) Итоговая проверка"
"${OPENCLAW_BIN}" skills check
echo
"${OPENCLAW_BIN}" channels status --probe || true
echo
echo "✅ Max setup завершён."
echo "ℹ️ Если менялся env/config, перезапусти gateway/krab launcher для применения."
read -r -p "Нажми Enter для закрытия..."
