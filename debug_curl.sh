#!/bin/bash
cd "$(dirname "$0")"
PROJECT_ROOT=$(pwd)

# 1. Start OpenClaw
OPENCLAW_DIR="$PROJECT_ROOT/scratch/openclaw_official"
pushd "$OPENCLAW_DIR" > /dev/null
NODE_CMD="node openclaw.mjs gateway run --bind loopback --port 18789"
$NODE_CMD > "$PROJECT_ROOT/openclaw_debug.log" 2>&1 &
PID=$!
popd > /dev/null

echo "⏳ Waiting 5s..."
sleep 5

echo "--- Testing /health ---"
curl -v http://127.0.0.1:18789/health

echo "--- Testing /v1/chat/completions (GET) ---"
curl -v http://127.0.0.1:18789/v1/chat/completions

echo "--- Testing /v1/chat/completions (POST without token) ---"
curl -v -X POST http://127.0.0.1:18789/v1/chat/completions

echo "🛑 Stopping..."
kill $PID
