#!/bin/bash
echo "Stopping OpenClaw Gateway..."
pkill -f "openclaw-gateway" || echo "OpenClaw Gateway not running."
pkill -f "openclaw" || echo "OpenClaw process not running."

echo "Waiting for ports to clear..."
sleep 2

echo "Starting OpenClaw Gateway..."
# Assuming openclaw is in PATH or use absolute path
/opt/homebrew/bin/openclaw gateway --port 18789 > openclaw.log 2>&1 &

echo "Gateway started on port 18789. Logs in openclaw.log"
