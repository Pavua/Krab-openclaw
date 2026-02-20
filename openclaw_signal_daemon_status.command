#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon Status (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Показывает состояние launchd-сервиса signal-cli и probe статуса канала.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LABEL="ai.openclaw.signal-cli"
UID_VALUE="$(id -u)"

echo "🔎 Launchd status (${LABEL}):"
if launchctl print "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1; then
  echo "✅ Сервис загружен."
else
  echo "⚠️ Сервис не загружен."
fi

echo
echo "🔎 Порт Signal daemon (18080):"
if lsof -nP -iTCP:18080 -sTCP:LISTEN; then
  echo "✅ Порт слушается."
else
  echo "⚠️ Порт не слушается."
fi

if command -v openclaw >/dev/null 2>&1; then
  echo
  echo "🔎 OpenClaw channels status (signal):"
  openclaw channels status --probe | rg -i "Signal|probe failed|works|error" || true
fi
