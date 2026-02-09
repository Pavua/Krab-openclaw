#!/bin/bash
cd "$(dirname "$0")/.."
echo "🚀 Starting OpenClaw (Krab)..."
docker-compose up -d
echo "✅ OpenClaw started! Logs:"
docker-compose logs -f openclaw-gateway
