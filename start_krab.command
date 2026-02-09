#!/bin/bash
# Script to launch Krab Userbot on macOS
# Get the directory where this script is located
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Launch Terminal if not waiting
echo "🦀 Launching Krab Userbot..."
echo "📂 Directory: $DIR"

# Check for venv
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment 'venv' not found!"
    echo "Please run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    read -p "Press Enter to exit..."
    exit 1
fi

# Activate venv
# Activate venv
source venv/bin/activate

# Start OpenClaw Gateway in background
echo "🦀 Starting OpenClaw Gateway..."
# Assuming OpenCrawl is a sibling directory
OPENCLAW_BIN="$DIR/../OpenCrawl/node_modules/.bin/openclaw"

if [ -f "$OPENCLAW_BIN" ]; then
    "$OPENCLAW_BIN" gateway --port 18792 > openclaw.log 2>&1 &
    OPENCLAW_PID=$!
    echo "✅ OpenClaw started (PID $OPENCLAW_PID)"
else
    echo "⚠️ OpenClaw binary not found at $OPENCLAW_BIN"
    echo "Please ensure 'OpenCrawl' project is adjacent to this folder."
fi

# Give it a moment to initialize
sleep 3

# Run the bot
# Run the bot in a loop for auto-restart
while true; do
    echo "🚀 Starting Python Bot..."
    python3 -m src.main
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

# Keep window open if it crashes
# Cleanup on exit
if [ -n "$OPENCLAW_PID" ]; then
    kill "$OPENCLAW_PID"
    echo "🛑 OpenClaw stopped."
fi

echo "⚠️ Krab stopped."
read -p "Press Enter to close..."
