#!/bin/bash
cd "$(dirname "$0")"
echo "🛑 Stopping Krab..."
docker-compose down
echo "✅ Krab stopped."
sleep 2
