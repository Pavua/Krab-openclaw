#!/bin/bash
cd "$(dirname "$0")"
PROJECT_ROOT=$(pwd)

echo "🦀 Verifying OpenClaw Integration (Full Cycle)..."

# 0. Setup venv
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "⬇️  Checking dependencies..."
pip install python-dotenv aiohttp structlog google-genai > /dev/null 2>&1

# 1. Start OpenClaw Gateway in Background
echo "🚀 Starting OpenClaw Gateway (Port 18789)..."
OPENCLAW_DIR="$PROJECT_ROOT/scratch/openclaw_official"

if [ ! -d "$OPENCLAW_DIR" ]; then
    echo "❌ OpenClaw directory not found at $OPENCLAW_DIR"
    exit 1
fi

# Fix Config: Copy local config to ~/.openclaw/openclaw.json
# This ensures it finds the config without relying on env vars that might fail
LOCAL_CONFIG="$OPENCLAW_DIR/data/config/openclaw.json"
GLOBAL_CONFIG="$HOME/.openclaw/openclaw.json"
GLOBAL_DIR="$HOME/.openclaw"

if [ -f "$LOCAL_CONFIG" ]; then
    echo "🔧 Installing local config to $GLOBAL_CONFIG..."
    mkdir -p "$GLOBAL_DIR"
    # Backup if exists and not already backed up
    if [ -f "$GLOBAL_CONFIG" ] && [ ! -f "$GLOBAL_CONFIG.bak" ]; then
        mv "$GLOBAL_CONFIG" "$GLOBAL_CONFIG.bak"
    fi
    cp "$LOCAL_CONFIG" "$GLOBAL_CONFIG"
else
    echo "⚠️  Local config not found at $LOCAL_CONFIG"
fi

pushd "$OPENCLAW_DIR" > /dev/null
# Start node directly
NODE_CMD="node openclaw.mjs gateway run --bind loopback --port 18789"
# Log to file, background
$NODE_CMD > "$PROJECT_ROOT/openclaw_test.log" 2>&1 &
OPENCLAW_PID=$!
popd > /dev/null

echo "⏳ Waiting 10s for Gateway to launch (PID: $OPENCLAW_PID)..."
sleep 10

# Check if process is still running
if ! ps -p $OPENCLAW_PID > /dev/null; then
   echo "❌ OpenClaw failed to start! Check openclaw_test.log"
   cat "$PROJECT_ROOT/openclaw_test.log"
   exit 1
fi

# 2. Run Python Test with correct ENV
echo "🧪 Running Test..."
export OPENCLAW_URL="http://127.0.0.1:18789"
export OPENCLAW_TOKEN="sk-nexus-bridge"

python tests/test_openclaw.py
TEST_EXIT_CODE=$?

# 3. Cleanup
echo "🛑 Stopping OpenClaw..."
kill $OPENCLAW_PID
wait $OPENCLAW_PID 2>/dev/null

if [ $TEST_EXIT_CODE -eq 0 ]; then
    echo "✅ SUCCESS: OpenClaw Restored & Verified!"
    # Ensure user knows about the new config needed
    echo "⚠️  NOTE: Update your main .env with:"
    echo "OPENCLAW_URL=http://localhost:18789"
    echo "OPENCLAW_TOKEN=sk-nexus-bridge"
else
    echo "❌ FAILURE: Test failed."
    echo "--- OpenClaw Logs ---"
    cat "$PROJECT_ROOT/openclaw_test.log" | tail -n 20
fi

exit $TEST_EXIT_CODE
