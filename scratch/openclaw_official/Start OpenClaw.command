#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 Starting OpenClaw (Local)..."

# Load environment variables
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

# Point to the correct configuration file (with Google Gemini)
export OPENCLAW_CONFIG_PATH="$(pwd)/data/config/openclaw.json"

# Determine command
# Using 'gateway run' for foreground execution
CMD="node openclaw.mjs gateway run --bind loopback --port 18789"

echo "Using Config: $OPENCLAW_CONFIG_PATH"
echo "Running: $CMD"
$CMD
