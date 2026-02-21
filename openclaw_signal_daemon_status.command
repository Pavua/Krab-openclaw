#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon Status (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Показывает состояние launchd-сервиса signal-cli и probe статуса канала.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  set -a
  source ./.env
  set +a
fi

LABEL="ai.openclaw.signal-cli"
UID_VALUE="$(id -u)"
SIGNAL_HTTP_URL="${OPENCLAW_SIGNAL_HTTP_URL:-http://127.0.0.1:18080}"
URL_NO_PROTO="${SIGNAL_HTTP_URL#http://}"
URL_NO_PROTO="${URL_NO_PROTO#https://}"
SIGNAL_PORT="${URL_NO_PROTO##*:}"
if [[ "$SIGNAL_PORT" == "$URL_NO_PROTO" || -z "$SIGNAL_PORT" ]]; then
  SIGNAL_PORT="18080"
fi

echo "🔎 Launchd status (${LABEL}):"
if launchctl print "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1; then
  echo "✅ Сервис загружен."
else
  echo "⚠️ Сервис не загружен."
fi

echo
echo "🔎 Порт Signal daemon (${SIGNAL_PORT}):"
if lsof -nP -iTCP:"${SIGNAL_PORT}" -sTCP:LISTEN; then
  echo "✅ Порт слушается."
else
  echo "⚠️ Порт не слушается."
fi

if command -v openclaw >/dev/null 2>&1; then
  echo
  echo "🔎 OpenClaw channels status (signal):"
  openclaw channels status --probe | rg -i "Signal|probe failed|works|error" || true
fi
