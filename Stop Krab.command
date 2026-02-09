#!/bin/bash
# 🛑 Stop Krab 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🛑 Stopping Krab System..."

# Убиваем юзербота
pkill -f "src.main" && echo "✅ Userbot stopped." || echo "ℹ️ Userbot not running."

# Убиваем OpenClaw
if [ -f .openclaw.pid ]; then
    PID=$(cat .openclaw.pid)
    kill $PID 2>/dev/null && echo "✅ OpenClaw Gateway stopped."
    rm .openclaw.pid
else
    pkill -f "openclaw gateway" && echo "✅ OpenClaw Gateway killed." || echo "ℹ️ OpenClaw not running."
fi

# Очистка логов
echo "🧹 Cleaning session files..."
rm -f *.session-journal *.session-wal

echo "✨ Done."
sleep 2
