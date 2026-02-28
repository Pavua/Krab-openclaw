#!/bin/bash
# 🦀 Krab Userbot — Standalone Launcher (macOS)

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🦀 Launching Krab Userbot..."
echo "📂 Directory: $DIR"

# === Виртуальное окружение ===
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ Virtual environment not found (.venv or venv)!"
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    read -p "Press Enter to exit..."
    exit 1
fi

# === Загрузка .env ===
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "⚠️ .env file not found!"
fi

# === OpenClaw Gateway ===
OPENCLAW_BIN="/opt/homebrew/bin/openclaw"
if [ ! -x "$OPENCLAW_BIN" ]; then
    OPENCLAW_BIN=$(which openclaw 2>/dev/null)
fi

if [ -n "$OPENCLAW_BIN" ]; then
    if ! pgrep -f "openclaw gateway" > /dev/null; then
        echo "🦞 Starting OpenClaw Gateway..."
        nohup "$OPENCLAW_BIN" gateway > openclaw.log 2>&1 &
        echo $! > .openclaw.pid
        echo "✅ OpenClaw started (PID $!)"
        sleep 3
    else
        echo "✅ OpenClaw Gateway already running"
    fi
else
    echo "⚠️ OpenClaw binary not found. AI features may not work."
fi

# === Запуск бота с авто-рестартом ===
while true; do
    echo "🚀 Starting Krab..."
    python -m src.main
    EXIT_CODE=$?

    if [ $EXIT_CODE -eq 42 ]; then
        echo "🔄 Restart requested (Code 42)..."
        sleep 1
        continue
    elif [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Bot stopped cleanly."
        break
    else
        echo "⚠️ Bot crashed (Code $EXIT_CODE). Restarting in 5 seconds..."
        sleep 5
    fi
done

# === Cleanup ===
if [ -f .openclaw.pid ]; then
    PID=$(cat .openclaw.pid)
    kill "$PID" 2>/dev/null && echo "🛑 OpenClaw stopped."
    rm -f .openclaw.pid
fi

echo "🦀 Krab stopped."
read -p "Press Enter to close..."
