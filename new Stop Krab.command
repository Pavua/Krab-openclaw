#!/bin/bash
# 🛑 Stop Krab 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🛑 Stopping Krab System..."

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

# 0. Кидаем "ядовитую таблетку", чтобы скрипт старта сам вышел из цикла!
touch .stop_krab

# 1. Убиваем скрипты авто-рестарта
pkill -f "start_krab" || true
pkill -f "run_krab" || true

# 2. Убиваем Python-процессы, запущенные из папки Краба
pkill -f "$DIR/src/main" && echo "✅ Userbot (main) stopped." || true
pkill -f "python.*src\.main" && echo "✅ Userbot (fallback) stopped." || true

# 3. Чистим порт web-панели.
clear_web_port 8080

# 4. Останавливаем OpenClaw
"${DIR}/.venv/bin/openclaw" gateway stop >/dev/null 2>&1 || openclaw gateway stop >/dev/null 2>&1 || true
pkill -f "openclaw-gateway" >/dev/null 2>&1 || true
if [ -f .openclaw.pid ]; then
    PID=$(cat .openclaw.pid)
    kill $PID 2>/dev/null && echo "✅ OpenClaw Gateway stopped (PID $PID)."
    rm -f .openclaw.pid
else
    pkill -f "openclaw gateway" && echo "✅ OpenClaw Gateway killed." || echo "ℹ️ OpenClaw not running."
fi

# 5. Останавливаем Docker (на случай, если он работает в фоне)
if command -v docker &> /dev/null; then
    docker stop krab-ai-bot >/dev/null 2>&1 && echo "✅ Docker container stopped." || true
fi

echo "✨ Done."
sleep 2
