#!/bin/bash
cd "$(dirname "$0")"

# --- Configuration ---
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
PID_FILE="krab.pid"

# Explicit paths to environments
PROJECT_ROOT=$(pwd)
CORE_DIR="$PROJECT_ROOT/openclaw_official"
BRIDGE_DIR="$PROJECT_ROOT/openclaw_official/nexus_bridge"
# Explicitly use python3.13 as pip installed packages there
VENV_PYTHON="$BRIDGE_DIR/venv/bin/python3.13"

echo "🦀 Launching Krab-Ultimate System..."
echo "📂 Root: $PROJECT_ROOT"
echo "🐍 Python: $VENV_PYTHON"

# 1. Start OpenClaw Core (Gateway)
echo "🧠 Starting Brain (OpenClaw Core)..."
# Using --port explicitly to match config
/opt/homebrew/bin/node openclaw_official/scripts/run-node.mjs gateway --port 18789 > "$LOG_DIR/core.log" 2>&1 &
CORE_PID=$!
echo "   PID: $CORE_PID"

# 1.5 Start OpenClaw Dashboard
echo "📊 Starting Dashboard (Web UI)..."
(cd openclaw_official && /opt/homebrew/bin/node openclaw.mjs dashboard) > "$LOG_DIR/dashboard.log" 2>&1 &
DASH_PID=$!

# Wait for Gateway
sleep 2

# 2. Start Nexus Bridge (Telegram Userbot)
echo "🌉 Starting Nexus Bridge..."
"$VENV_PYTHON" openclaw_official/nexus_bridge/main.py > "$LOG_DIR/bridge.log" 2>&1 &
BRIDGE_PID=$!
echo "   PID: $BRIDGE_PID"

# 3. Start The Ear (Voice UI)
echo "👂 Starting Ear UI..."
# Use the VENV python explicitly
"$VENV_PYTHON" openclaw_official/nexus_bridge/ear_ui.py > "$LOG_DIR/ear.log" 2>&1 &
EAR_PID=$!
echo "   PID: $EAR_PID"

# Save PIDs
echo "$CORE_PID" > "$PID_FILE"
echo "$BRIDGE_PID" >> "$PID_FILE"
echo "$EAR_PID" >> "$PID_FILE"

echo "✅ System Started!"
echo "   Logs in $LOG_DIR/"

# Keep script running to monitor processes (prevents "Crash" look)
wait $CORE_PID $BRIDGE_PID $EAR_PID
