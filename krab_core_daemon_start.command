#!/bin/zsh
# -----------------------------------------------------------------------------
# Krab Core LaunchAgent Start (macOS)
# Создаёт/обновляет LaunchAgent и запускает ядро как фоновый сервис.
# Это защищает Krab от остановки при закрытии терминала/IDE.
# -----------------------------------------------------------------------------

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
LABEL="ai.krab.core"
UID_NUM="$(id -u)"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
LOG_DIR="$PROJECT_ROOT/logs"

resolve_python() {
  if [[ -x "$PROJECT_ROOT/.venv/bin/python3" ]]; then
    echo "$PROJECT_ROOT/.venv/bin/python3"
    return 0
  fi
  if [[ -x "$PROJECT_ROOT/.venv_krab/bin/python3" ]]; then
    echo "$PROJECT_ROOT/.venv_krab/bin/python3"
    return 0
  fi
  echo "python3"
}

PYTHON_BIN="$(resolve_python)"
NODE_BIN="$(command -v node || true)"
if [[ -n "$NODE_BIN" ]]; then
  NODE_DIR="$(dirname "$NODE_BIN")"
else
  NODE_DIR="/opt/homebrew/bin"
fi
PATH_VALUE="$NODE_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "$PLIST_DIR" "$LOG_DIR" "$PROJECT_ROOT/.runtime"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON_BIN</string>
    <string>-m</string>
    <string>src.main</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_ROOT</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>$PROJECT_ROOT</string>
    <key>PATH</key>
    <string>$PATH_VALUE</string>
  </dict>

  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/krab_launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/krab_launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST_PATH"
launchctl enable "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

sleep 2
echo "✅ Krab Core LaunchAgent запущен: $LABEL"
launchctl print "gui/$UID_NUM/$LABEL" | rg -n "state =|pid =|last exit code =|path =" || true
echo "Логи: $LOG_DIR/krab_launchd.out.log и $LOG_DIR/krab_launchd.err.log"
