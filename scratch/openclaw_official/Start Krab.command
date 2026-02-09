#!/bin/bash
cd "$(dirname "$0")"
echo "🚀 Starting Krab (OpenClaw)..."
docker-compose up -d
echo "✅ Krab started! Logs will appear below (Ctrl+C to exit logs, Krab keeps running):"
docker-compose logs -f openclaw-gateway
