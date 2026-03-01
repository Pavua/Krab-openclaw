#!/bin/bash
# 🛑 Stop Krab 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🛑 Stopping Krab System..."

# Останавливаем юзербота
pkill -f "src.main" && echo "✅ Userbot stopped." || echo "ℹ️ Userbot not running."

# Останавливаем OpenClaw
if [ -f .openclaw.pid ]; then
    PID=$(cat .openclaw.pid)
    kill $PID 2>/dev/null && echo "✅ OpenClaw Gateway stopped (PID $PID)."
    rm -f .openclaw.pid
else
    pkill -f "openclaw gateway" && echo "✅ OpenClaw Gateway killed." || echo "ℹ️ OpenClaw not running."
fi

# Очистка временных файлов
echo "🧹 Cleaning session files..."
rm -f *.session-journal *.session-wal

echo "✨ Done."
sleep 2
