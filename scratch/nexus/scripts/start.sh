#!/bin/bash

# Universal Launcher
# 1. Starts LM Studio (if requested)
# 2. Starts OpenClaw Gateway (Node.js) in background
# 3. Starts Nexus Userbot (Python) in foreground

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
ROOT_DIR="$(dirname "$DIR")/.."

echo "🚀 Nexus Universal Launcher"
echo "=========================="

# 1. LM Studio Check
if pgrep -x "LM Studio" > /dev/null; then
    echo "✅ LM Studio is running."
else
    echo "⚠️ LM Studio is NOT running."
    read -p "   Start LM Studio? (y/n) " -n 1 -r
    echo 
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        open -a "LM Studio"
        echo "⏳ Waiting for LM Studio..."
        sleep 10
    fi
fi

# 2. Start OpenClaw Gateway
echo "🔮 app: OpenClaw Gateway..."
cd "$ROOT_DIR/openclaw_official" || exit

# Check if node dependencies exist
if [ ! -d "node_modules" ]; then
    echo "📦 Installing OpenClaw dependencies (first run)..."
    npm install
fi

# START GATEWAY IN BACKGROUND
# We log to a file so we don't clutter the userbot output
# START GATEWAY IN BACKGROUND
# We check for an existing process first to avoid conflicts
pkill -f "openclaw gateway" || true

# Specific command to launch the WebSocket Gateway
nohup node scripts/run-node.mjs gateway run --bind loopback --port 18789 --force > "$ROOT_DIR/openclaw.log" 2>&1 &
GATEWAY_PID=$!
echo "   Gateway started (PID: $GATEWAY_PID). Logs: openclaw.log"
echo "   Waiting for port 18789..."

# Wait for port 18789 to be active
attempts=0
while ! lsof -i :18789 > /dev/null; do
    sleep 1
    attempts=$((attempts+1))
    if [ $attempts -ge 30 ]; then
        echo "❌ Gateway failed to start in 30s. Check openclaw.log."
        exit 1
    fi
done
echo "✅ Gateway active!"

# 3. Start Nexus Userbot
echo "🤖 app: Nexus Userbot..."
cd "$ROOT_DIR/nexus" || exit
python3 run.py

# Cleanup on exit
kill $GATEWAY_PID
