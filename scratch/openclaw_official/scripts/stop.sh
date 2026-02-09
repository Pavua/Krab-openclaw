#!/bin/bash
cd "$(dirname "$0")/.."
echo "🛑 Stopping OpenClaw..."
docker-compose down
echo "✅ OpenClaw stopped."
