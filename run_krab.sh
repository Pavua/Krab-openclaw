#!/bin/bash
# 🦀 Запуск Краба одной кнопкой (Full Stack)

cd "$(dirname "$0")"

# === 0. Pre-Flight Cleanup ===
echo "🧹 Cleaning up previous instances..."
pkill -f "src.main" || true
rm -f *.session-journal *.session-wal
sleep 1

echo "🦀 Starting Krab AI Userbot Full Stack..."

# === 1. Cleanup on Exit ===
cleanup() {
    echo "🛑 Stopping..."
    if [ -f .openclaw.pid ]; then
        PID=$(cat .openclaw.pid)
        if ps -p $PID > /dev/null 2>&1; then
            echo "Killing OpenClaw (PID $PID)..."
            kill $PID
        fi
        rm -f .openclaw.pid
    fi
    exit
}
trap cleanup SIGINT SIGTERM

# === 2. Проверка OpenClaw Gateway ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
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

# === 3. Загрузка .env ===
if [ -f .env ]; then
    echo "⚙️ Loading environment variables..."
    set -a
    source .env
    set +a
else
    echo "⚠️ .env file not found!"
    exit 1
fi

# === 4. Виртуальное окружение ===
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
fi

# === 5. Зависимости ===
pip install -q -r requirements.txt

# === 6. Запуск Бота (Loop for Restarts) ===
echo "🚀 Launching Krab Userbot..."

while true; do
    python -u -m src.main 2>&1 | tee -a krab.log
    EXIT_CODE=${PIPESTATUS[0]}

    if [ $EXIT_CODE -eq 42 ]; then
        echo "🔄 Krab requested restart (Code 42). Rebooting in 2s..."
        sleep 2
    elif [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Krab stopped cleanly."
        break
    else
        echo "⚠️ Krab crashed (Code $EXIT_CODE). Restarting in 5s..."
        sleep 5
    fi
done
