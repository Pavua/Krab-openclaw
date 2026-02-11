#!/bin/bash
cd "$(dirname "$0")"
PROJECT_ROOT=$(pwd)

echo "🦀 Starting OpenClaw Gateway for Krab..."

# 1. Config Fix (Ensure global config is valid)
OPENCLAW_DIR="$PROJECT_ROOT/scratch/openclaw_official"
LOCAL_CONFIG="$OPENCLAW_DIR/data/config/openclaw.json"
GLOBAL_CONFIG="$HOME/.openclaw/openclaw.json"
GLOBAL_DIR="$HOME/.openclaw"

if [ -f "$LOCAL_CONFIG" ]; then
    # Check if global config exists, if not or if we just want to be sure, we copy.
    # We only copy if they differ to avoid unnecessary writes, or just copy to be safe.
    # For now, let's copy to be safe as we know local is the "Source of Truth" for this setup.
    mkdir -p "$GLOBAL_DIR"
    cp "$LOCAL_CONFIG" "$GLOBAL_CONFIG"
    echo "✅ Configuration synced to ~/.openclaw/openclaw.json"
else
    echo "⚠️  Local config not found at $LOCAL_CONFIG. Using existing global config."
fi

# 2. Run OpenClaw
pushd "$OPENCLAW_DIR" > /dev/null
echo "🚀 Launching Gateway on port 18789..."

# Use node directly to avoid permission issues with intermediate scripts
node openclaw.mjs gateway run --bind loopback --port 18789

popd > /dev/null
