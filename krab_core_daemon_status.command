#!/bin/zsh
# -----------------------------------------------------------------------------
# Krab Core LaunchAgent Status (macOS)
# Показывает состояние launchd-сервиса и последние строки логов.
# -----------------------------------------------------------------------------

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="ai.krab.core"
UID_NUM="$(id -u)"
OUT_LOG="$PROJECT_ROOT/logs/krab_launchd.out.log"
ERR_LOG="$PROJECT_ROOT/logs/krab_launchd.err.log"

echo "=== launchd status: $LABEL ==="
if launchctl print "gui/$UID_NUM/$LABEL" >/tmp/krab_launchd_status.txt 2>/dev/null; then
  cat /tmp/krab_launchd_status.txt | rg -n "state =|pid =|last exit code =|path =" || true
else
  echo "Сервис не загружен."
fi

echo ""
echo "=== tail: $OUT_LOG ==="
tail -n 30 "$OUT_LOG" 2>/dev/null || echo "Лог пока пуст."

echo ""
echo "=== tail: $ERR_LOG ==="
tail -n 30 "$ERR_LOG" 2>/dev/null || echo "Лог пока пуст."
