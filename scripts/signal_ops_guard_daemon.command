#!/bin/zsh
# ----------------------------------------------------------------------------
# Signal Ops Guard Daemon (launchd)
# ----------------------------------------------------------------------------
# Запускает фонового мониторинга Signal-канала с автоалертами.
#
# Команды:
#   ./scripts/signal_ops_guard_daemon.command start
#   ./scripts/signal_ops_guard_daemon.command status
#   ./scripts/signal_ops_guard_daemon.command stop
#   ./scripts/signal_ops_guard_daemon.command logs [N]
# ----------------------------------------------------------------------------

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

LABEL="ai.krab.signal-ops-guard"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_VALUE="$(id -u)"
ACTION="${1:-start}"
LINES="${2:-80}"
PY_BIN="$(command -v python3)"

mkdir -p "$ROOT_DIR/logs"

case "$ACTION" in
  start)
    cat > "$PLIST_PATH" <<EOF2
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY_BIN}</string>
    <string>${ROOT_DIR}/scripts/signal_ops_guard.py</string>
    <string>--interval-sec</string>
    <string>60</string>
    <string>--lines</string>
    <string>120</string>
    <string>--cooldown-sec</string>
    <string>900</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>StandardOutPath</key>
  <string>${ROOT_DIR}/logs/signal-ops-guard.out.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT_DIR}/logs/signal-ops-guard.err.log</string>
</dict>
</plist>
EOF2

    launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/${UID_VALUE}" "$PLIST_PATH"
    launchctl enable "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
    launchctl kickstart -k "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true

    echo "✅ Signal Ops Guard daemon запущен: ${LABEL}"
    echo "   Status: ./scripts/signal_ops_guard_daemon.command status"
    echo "   Logs:   ./scripts/signal_ops_guard_daemon.command logs 120"
    ;;

  status)
    echo "🔎 Launchd status (${LABEL}):"
    if launchctl print "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1; then
      echo "✅ Сервис загружен."
    else
      echo "⚠️ Сервис не загружен."
    fi
    echo
    echo "🧾 Последние alerts (если есть):"
    if [[ -f "$ROOT_DIR/artifacts/ops/signal_guard_alerts.jsonl" ]]; then
      tail -n 5 "$ROOT_DIR/artifacts/ops/signal_guard_alerts.jsonl" || true
    else
      echo "(ещё нет alert-записей)"
    fi
    ;;

  stop)
    launchctl bootout "gui/${UID_VALUE}/${LABEL}" >/dev/null 2>&1 || true
    echo "🛑 Signal Ops Guard daemon остановлен."
    ;;

  logs)
    echo "📄 logs/signal-ops-guard.out.log (tail ${LINES})"
    tail -n "$LINES" "$ROOT_DIR/logs/signal-ops-guard.out.log" 2>/dev/null || true
    echo
    echo "📄 logs/signal-ops-guard.err.log (tail ${LINES})"
    tail -n "$LINES" "$ROOT_DIR/logs/signal-ops-guard.err.log" 2>/dev/null || true
    ;;

  *)
    echo "❌ Неизвестное действие: ${ACTION}"
    echo "Использование: $0 {start|status|stop|logs [N]}"
    exit 2
    ;;
esac
