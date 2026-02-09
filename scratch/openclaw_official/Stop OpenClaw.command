#!/bin/bash
echo "🛑 Stopping OpenClaw..."
pkill -f "openclaw.mjs"
pkill -f "dist/index.js"
echo "✅ OpenClaw stopped."
