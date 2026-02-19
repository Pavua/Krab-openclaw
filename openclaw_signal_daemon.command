#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Читает OPENCLAW_SIGNAL_NUMBER и OPENCLAW_SIGNAL_HTTP_URL из .env.
# 2) Синхронизирует канал signal в OpenClaw config.
# 3) Запускает signal-cli daemon --http на выделенном порту.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source ./.env
  set +a
fi

if ! command -v signal-cli >/dev/null 2>&1; then
  echo "❌ signal-cli не найден. Установи: brew install signal-cli"
  exit 1
fi

SIGNAL_NUMBER="${OPENCLAW_SIGNAL_NUMBER:-}"
SIGNAL_HTTP_URL="${OPENCLAW_SIGNAL_HTTP_URL:-http://127.0.0.1:18080}"

if [[ -z "$SIGNAL_NUMBER" ]]; then
  echo "❌ OPENCLAW_SIGNAL_NUMBER не задан в .env"
  exit 1
fi

URL_NO_PROTO="${SIGNAL_HTTP_URL#http://}"
URL_NO_PROTO="${URL_NO_PROTO#https://}"
SIGNAL_HOST="${URL_NO_PROTO%%:*}"
SIGNAL_PORT="${URL_NO_PROTO##*:}"

if [[ "$SIGNAL_HOST" == "$SIGNAL_PORT" || -z "$SIGNAL_PORT" ]]; then
  SIGNAL_HOST="127.0.0.1"
  SIGNAL_PORT="18080"
fi

echo "🔧 Signal target: number=${SIGNAL_NUMBER}, http=${SIGNAL_HOST}:${SIGNAL_PORT}"

if command -v openclaw >/dev/null 2>&1; then
  echo "⏳ Синхронизирую канал Signal в OpenClaw..."
  openclaw channels add --channel signal --signal-number "$SIGNAL_NUMBER" --http-url "$SIGNAL_HTTP_URL" >/dev/null 2>&1 || true
fi

echo "⏳ Запускаю signal-cli daemon (foreground)..."
echo "   Остановить: Ctrl+C"
echo
signal-cli -a "$SIGNAL_NUMBER" daemon --http "${SIGNAL_HOST}:${SIGNAL_PORT}"
