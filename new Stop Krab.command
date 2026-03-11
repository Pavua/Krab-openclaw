#!/bin/bash
# 🛑 Stop Krab 🦀
# Назначение: безопасная остановка Krab/OpenClaw без убийства посторонних процессов по stale PID.
# Связи: парный скрипт к new start_krab.command и Full Ecosystem stop.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🛑 Stopping Krab System..."

LAUNCHER_LOCK_FILE="$DIR/.krab_launcher.lock"
OPENCLAW_PID_FILE="$DIR/.openclaw.pid"
OPENCLAW_OWNER_FILE="$DIR/.openclaw.owner"
KRAB_PROC_PATTERN="[Pp]ython.*src\\.main"

is_pid_alive() {
    local pid="$1"
    [ -n "${pid:-}" ] && kill -0 "$pid" >/dev/null 2>&1
}

is_openclaw_gateway_pid() {
    local pid="$1"
    [ -n "${pid:-}" ] || return 1
    local cmd
    cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
    echo "$cmd" | grep -E "openclaw( |$).*gateway( |$)|openclaw-gateway" >/dev/null 2>&1
}

disable_legacy_launchd_core() {
    # Отключаем KeepAlive launchd-сервис, иначе src.main мгновенно поднимется снова.
    launchctl bootout gui/$(id -u)/ai.krab.core >/dev/null 2>&1 || true
    launchctl bootout user/$(id -u)/ai.krab.core >/dev/null 2>&1 || true
    launchctl remove ai.krab.core >/dev/null 2>&1 || true
}

clear_web_port() {
    local port="${1:-8080}"
    local pids
    pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
    if [ -z "$pids" ]; then
        echo "✅ Port ${port} is clear."
        return 0
    fi
    echo "👻 Found listeners on port ${port}: $pids"
    echo "$pids" | xargs kill -TERM 2>/dev/null || true
    sleep 0.5
    pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "⚠️ Force killing port ${port} listeners..."
        echo "$pids" | xargs kill -KILL 2>/dev/null || true
        sleep 0.3
    fi
    pids=$(lsof -t -i "tcp:${port}" -sTCP:LISTEN 2>/dev/null)
    [ -z "$pids" ] && echo "✅ Port ${port} cleared." || echo "❌ Port ${port} still occupied: $pids"
}

resolve_openclaw_bin() {
    if [ -x "${DIR}/.venv/bin/openclaw" ]; then
        echo "${DIR}/.venv/bin/openclaw"
        return 0
    fi
    if command -v openclaw >/dev/null 2>&1; then
        command -v openclaw
        return 0
    fi
    return 1
}

safe_openclaw_control() {
    local timeout_sec="${1:-8}"
    shift
    local openclaw_bin
    openclaw_bin="$(resolve_openclaw_bin)" || return 127

    # На реальном macOS runtime `openclaw browser/gateway stop` может повиснуть,
    # если backend уже умер, а CLI всё ещё ждёт RPC-ответ. Для stop-script это
    # особенно опасно: пользователь видит "Stop" и думает, что всё завершилось.
    python3 - "$openclaw_bin" "$timeout_sec" "$@" <<'PY'
import subprocess
import sys

bin_path = sys.argv[1]
timeout_sec = float(sys.argv[2])
args = [bin_path, *sys.argv[3:]]

try:
    completed = subprocess.run(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout_sec,
        check=False,
    )
except subprocess.TimeoutExpired:
    raise SystemExit(124)
except FileNotFoundError:
    raise SystemExit(127)

raise SystemExit(int(completed.returncode))
PY
}

# 0. Кидаем "ядовитую таблетку", чтобы скрипт старта сам вышел из цикла!
touch .stop_krab
disable_legacy_launchd_core

# 1. Мягко просим завершиться, чтобы Pyrogram успел сохранить/закрыть сессию.
pkill -TERM -f "$DIR/src/main" >/dev/null 2>&1 || true
pkill -TERM -f "$KRAB_PROC_PATTERN" >/dev/null 2>&1 || true

# Даём процессу время закрыться корректно.
for i in 1 2 3 4 5 6 7 8; do
    sleep 0.5
    if ! pgrep -f "$KRAB_PROC_PATTERN" >/dev/null 2>&1; then
        echo "✅ Userbot stopped gracefully."
        break
    fi
done

# Если всё ещё жив — только тогда форс.
if pgrep -f "$KRAB_PROC_PATTERN" >/dev/null 2>&1; then
    echo "⚠️ Userbot still running, forcing stop..."
    pkill -KILL -f "$KRAB_PROC_PATTERN" >/dev/null 2>&1 || true
fi

# 2. Останавливаем скрипты авто-рестарта
pkill -f "start_krab" >/dev/null 2>&1 || true
pkill -f "run_krab" >/dev/null 2>&1 || true

# 3. Чистим порт web-панели.
clear_web_port 8080

# 4. Останавливаем OpenClaw
safe_openclaw_control 8 browser stop || true
# Подчищаем именно automation Chrome relay OpenClaw, не трогая обычный профиль пользователя.
pkill -f "remote-debugging-port=18800" >/dev/null 2>&1 || true
pkill -f "${HOME}/.openclaw/browser/openclaw/user-data" >/dev/null 2>&1 || true
safe_openclaw_control 8 gateway stop || true

if [ -f "$OPENCLAW_PID_FILE" ]; then
    PID=$(cat "$OPENCLAW_PID_FILE" 2>/dev/null || true)
    if is_openclaw_gateway_pid "$PID"; then
        kill "$PID" >/dev/null 2>&1 || true
        echo "✅ OpenClaw Gateway остановлен (PID $PID)."
    else
        echo "ℹ️ Пропускаю kill по stale PID ($PID): процесс не похож на openclaw gateway."
    fi
fi
pkill -f "openclaw-gateway" >/dev/null 2>&1 || true
rm -f "$OPENCLAW_PID_FILE" "$OPENCLAW_OWNER_FILE" "$LAUNCHER_LOCK_FILE"

# 5. Останавливаем Docker (на случай, если он работает в фоне)
if command -v docker &> /dev/null; then
    docker stop krab-ai-bot >/dev/null 2>&1 && echo "✅ Docker container stopped." || true
fi

echo "✨ Done."
sleep 2
