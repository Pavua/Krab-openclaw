#!/bin/zsh
# -----------------------------------------------------------------------------
# Signal Daemon Stop (one-click) для OpenClaw
# -----------------------------------------------------------------------------
# Останавливает launchd-сервис signal-cli и проверяет, что порт освобождён.
# -----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

LABEL="ai.openclaw.signal-cli"
UID_VALUE="$(id -u)"

echo "⏹ Останавливаю launchd сервис ${LABEL}..."
launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
launchctl disable "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true

# На случай хвостовых процессов
pkill -f "signal-cli.*daemon.*--http" >/dev/null 2>&1 || true

sleep 0.5

if lsof -nP -iTCP:18080 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "⚠️ Порт 18080 всё ещё занят. Проверь вручную: lsof -nP -iTCP:18080 -sTCP:LISTEN"
  exit 1
fi

echo "✅ Signal daemon остановлен."
