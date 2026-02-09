#!/bin/bash
DIR="$(dirname "$0")"
cd "$DIR"
# Activate virtual environment
source "$DIR/openclaw_official/nexus_bridge/venv/bin/activate"

echo "----------------------------------------------------------------"
echo "✅ Krab Ear: Crash Fix Applied. Audio Duration Logging Enabled."
echo "----------------------------------------------------------------"
echo "🦀 Launching Krab Ear..."
# Load .env from openclaw_official if exists
if [ -f "./openclaw_official/.env" ]; then
  # Safe export that handles whitespace better
  set -a
  source "./openclaw_official/.env"
  set +a
fi

./openclaw_official/nexus_bridge/venv/bin/python3 KrabEar/main.py
