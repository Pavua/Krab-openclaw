#!/bin/bash
echo "🩹 Repairing OpenClaw Gateway (Attempt 2)..."
cd openclaw_official
pkill -f "dist/index.js gateway"
# Use 'loopback' instead of '127.0.0.1' which caused the crash
OPENCLAW_IMAGE=ghcr.io/openclaw/core:latest \
OPENCLAW_GATEWAY_PORT=18789 \
OPENCLAW_GATEWAY_BIND=loopback \
nohup node dist/index.js gateway --bind loopback --port 18789 --allow-unconfigured > gateway.log 2>&1 &
echo "✅ Gateway Restarted with '--bind loopback'"
