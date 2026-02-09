#!/bin/bash
cd "$(dirname "$0")"
PID_FILE="krab_system.pids"

if [ -f "$PID_FILE" ]; then
    echo "🛑 Stopping Krab System..."
    PIDS=$(cat "$PID_FILE")
    for PID in $PIDS; do
        if ps -p $PID > /dev/null; then
            echo "   Killing $PID..."
            kill $PID
        else
            echo "   PID $PID not running."
        fi
    done
    rm "$PID_FILE"
    # Aggressive cleanup: Kill process listening on port 18789 if it survived
echo "🧹 Force cleaning port 18789..."
lsof -ti:18789 | xargs kill -9 2>/dev/null

# Clean python processes related to Krab
pkill -f "nexus_bridge/main.py"
pkill -f "nexus_bridge/ear"

echo "✅ System Stopped."
else
    echo "⚠️ No running system found (PID file missing)."
fi
