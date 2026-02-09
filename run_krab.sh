#!/bin/bash
# 🦀 Запуск Краба одной кнопкой (Full Stack)

# Перейти в директорию скрипта
cd "$(dirname "$0")"

# === 0. Pre-Flight Cleanup ===
echo "🧹 Cleaning up previous instances..."
pkill -f "src.main" || true
pkill -f "pure_test" || true
pkill -f "simple_run" || true
# Удаляем временные файлы блокировки базы (если есть)
rm -f *.session-journal *.session-wal
sleep 1

echo "🦀 Starting Krab AI Userbot Full Stack..."

# === 0. Cleanup on Exit ===
cleanup() {
    echo "🛑 Stopping..."
    if [ -f .openclaw.pid ]; then
        PID=$(cat .openclaw.pid)
        if ps -p $PID > /dev/null; then
            echo "Killing OpenClaw (PID $PID)..."
            kill $PID
        fi
        rm .openclaw.pid
    fi
    exit
}
trap cleanup SIGINT SIGTERM

# === 1. Проверка OpenClaw ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw)
fi

if [ -z "$OPENCLAW_BIN" ]; then
    echo "⚠️ OpenClaw binary not found. AI features may not work."
else
    if ! pgrep -f "openclaw gateway" > /dev/null; then
        echo "🦞 Starting OpenClaw Gateway..."
        nohup "$OPENCLAW_BIN" gateway > openclaw.log 2>&1 &
        echo $! > .openclaw.pid
        echo "   (OpenClaw logs: openclaw.log)"
        sleep 5
    else
        echo "✅ OpenClaw Gateway already running"
    fi
fi

# === 2. Загрузка .env ===
if [ -f .env ]; then
    echo "⚙️ Loading environment variables..."
    export $(grep -v '^#' .env | xargs)
else
    echo "⚠️ .env file not found!"
    exit 1
fi

# === 3. Виртуальное окружение ===
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# === 4. Зависимости ===
pip install -q -r requirements.txt

# === 5. MCP Серверы ===
if [ ! -d "mcp-servers/node_modules" ]; then
    echo "📦 Installing MCP servers..."
    chmod +x scripts/setup_mcp.sh
    ./scripts/setup_mcp.sh || echo "⚠️ MCP install failed, continuing..."
fi

# === 6. Запуск Бота (Loop for Restarts) ===
echo "🚀 Launching Krab Userbot..."

while true; do
    python -u -m src.main > krab.log 2>&1
    EXIT_CODE=$?
    
    if [ $EXIT_CODE -eq 42 ]; then
        echo "🔄 Krab requested restart (Code 42). Rebooting in 2s..."
        sleep 2
    else
        echo "🛑 Krab stopped with code $EXIT_CODE"
        break
    fi
done &

PID=$!
echo "Krab started with PID $PID (Loop)"
