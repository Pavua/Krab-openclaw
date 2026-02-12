#!/bin/bash
# Server Mode Launcher (Docker)
# v1.0
cd "$(dirname "$0")"

echo "🐳 Starting Krab in SERVER MODE (Docker)..."
echo "ℹ️  This mode provides isolation and stability."
echo "ℹ️  Dashboard: http://localhost:8080"

docker-compose up --build -d

echo ""
echo "✅ Krab is running in background."
echo "📜 To view logs: docker logs -f krab_v7"
echo "🛑 To stop: docker-compose down"
