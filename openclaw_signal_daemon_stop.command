#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon Stop (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Останавливает launchd-сервис signal-cli и проверяет, что порт освобождён.
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

echo "⏹ Останавливаю launchd сервис ${LABEL}..."
launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
launchctl disable "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true

# На случай хвостовых процессов
pkill -f "signal-cli.*daemon.*--http" >/dev/null 2>&1 || true

sleep 0.5

if lsof -nP -iTCP:"${SIGNAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠️ Порт ${SIGNAL_PORT} всё ещё занят. Проверь вручную: lsof -nP -iTCP:${SIGNAL_PORT} -sTCP:LISTEN"
  exit 1
fi

echo "✅ Signal daemon остановлен."
