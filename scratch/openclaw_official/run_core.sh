#!/bin/bash
cd "$(dirname "$0")"

# Load environment variables explicitly if needed.
# Enabling SKIP_CHANNELS to prevent the legacy Telegram bot from running.
export OPENCLAW_SKIP_CHANNELS=1
export FORCE_COLOR=1
export OPENCLAW_LOG_LEVEL=debug

# Install deps if node_modules missing
if [ ! -d "node_modules" ]; then
    echo "📦 Installing Dependencies..."
    npm install
fi

echo "🦀 Starting OpenClaw Core (Gateway Only)..."
# Explicitly run the 'gateway' command to start the server.
node scripts/run-node.mjs gateway --port 18789
