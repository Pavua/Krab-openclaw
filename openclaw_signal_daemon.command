#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Что делает:
# 1) Читает OPENCLAW_SIGNAL_NUMBER и OPENCLAW_SIGNAL_HTTP_URL из .env.
# 2) Синхронизирует канал signal в OpenClaw config.
# 3) Поднимает signal-cli daemon как launchd сервис (фон, автоперезапуск).
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

if ! signal-cli -a "$SIGNAL_NUMBER" listDevices >/dev/null 2>&1; then
  echo "❌ Signal номер не зарегистрирован в signal-cli: $SIGNAL_NUMBER"
  echo "   Сначала выполни: ./openclaw_signal_register.command"
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

mkdir -p "$ROOT_DIR/logs"
PLIST_PATH="$HOME/Library/LaunchAgents/ai.openclaw.signal-cli.plist"
LABEL="ai.openclaw.signal-cli"
UID_VALUE="$(id -u)"
TARGET_HTTP="${SIGNAL_HOST}:${SIGNAL_PORT}"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(command -v signal-cli)</string>
    <string>-a</string>
    <string>${SIGNAL_NUMBER}</string>
    <string>daemon</string>
    <string>--http</string>
    <string>${TARGET_HTTP}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>StandardOutPath</key>
  <string>${ROOT_DIR}/logs/signal-daemon.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT_DIR}/logs/signal-daemon.err.log</string>
</dict>
</plist>
EOF

echo "⏳ Перезапускаю launchd сервис ${LABEL}..."
launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID_VALUE}" "$PLIST_PATH"
launchctl enable "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true

echo "⏳ Ожидаю поднятие HTTP порта ${SIGNAL_PORT}..."
READY=0
for _ in {1..20}; do
  if lsof -nP -iTCP:"${SIGNAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 0.5
done

if [[ "$READY" -ne 1 ]]; then
  echo "❌ Signal daemon не поднял порт ${SIGNAL_PORT}."
  echo "   Проверь лог: ${ROOT_DIR}/logs/signal-daemon.err.log"
  exit 1
fi

if command -v openclaw >/dev/null 2>&1; then
  echo "⏳ Синхронизирую канал Signal в OpenClaw..."
  openclaw channels add --channel signal --signal-number "$SIGNAL_NUMBER" --http-url "$SIGNAL_HTTP_URL" >/dev/null 2>&1 || true
  echo "⏳ Проверяю статус Signal через probe..."
  openclaw channels status --probe | rg -i "Signal|probe failed|works" || true
fi

echo
echo "✅ Signal daemon запущен через launchd."
echo "   Stop: ./openclaw_signal_daemon_stop.command"
echo "   Status: ./openclaw_signal_daemon_status.command"
