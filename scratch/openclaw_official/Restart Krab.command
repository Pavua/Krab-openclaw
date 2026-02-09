#!/bin/bash
cd "$(dirname "$0")"
echo "🔄 Restarting Krab..."
docker-compose restart
echo "✅ Krab restarted."
sleep 2
