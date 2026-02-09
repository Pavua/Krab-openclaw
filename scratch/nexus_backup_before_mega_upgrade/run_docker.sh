#!/bin/bash
cd "$(dirname "$0")"

echo "🐳 Starting Nexus in Docker..."
docker-compose up -d --build
echo "✅ Nexus is running in background (Docker)."
echo "Logs: docker-compose logs -f"
