#!/bin/zsh
# -----------------------------------------------------------------------------
# Krab Core LaunchAgent Stop (macOS)
# Останавливает фоновый сервис ядра и выгружает LaunchAgent.
# -----------------------------------------------------------------------------

set -euo pipefail

LABEL="ai.krab.core"
UID_NUM="$(id -u)"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl disable "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true

if [[ -f "$PLIST_PATH" ]]; then
  rm -f "$PLIST_PATH"
fi

echo "🛑 Krab Core LaunchAgent остановлен: $LABEL"
