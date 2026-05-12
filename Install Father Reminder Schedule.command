#!/bin/zsh
# -*- coding: utf-8 -*-
#
# Устанавливает macOS LaunchAgent для периодических напоминаний отцу.
# Связь с проектом: вызывает `krab_father_reminder.py run-due`, поэтому
# частота, first-time guard, аудит и Telegram userbot остаются в одном контуре.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$REPO_DIR/venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/com.krab.father-reminder.plist"
LOG_DIR="$HOME/.openclaw/krab_runtime_state/logs"
mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.krab.father-reminder</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$REPO_DIR/scripts/agent_tools/krab_father_reminder.py</string>
    <string>run-due</string>
    <string>--channel</string>
    <string>telegram</string>
    <string>--confirm-send</string>
    <string>--first-time-confirm</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>WorkingDirectory</key>
  <string>$REPO_DIR</string>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/father_reminder_launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/father_reminder_launchd.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "LaunchAgent установлен: $PLIST_PATH"
echo "Проверка dry-run:"
"$PYTHON" "$REPO_DIR/scripts/agent_tools/krab_father_reminder.py" run-due --channel telegram --dry-run
echo
echo "Логи:"
echo "$LOG_DIR/father_reminder_launchd.out.log"
echo "$LOG_DIR/father_reminder_launchd.err.log"
