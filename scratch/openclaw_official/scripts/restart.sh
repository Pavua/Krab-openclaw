#!/bin/bash
cd "$(dirname "$0")/.."
echo "🔄 Restarting OpenClaw..."
docker-compose restart
echo "✅ OpenClaw restarted."
