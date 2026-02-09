#!/bin/bash
cd "$(dirname "$0")/.."
# Switch to cloud
sed -i '' 's/LLM_PROVIDER=local/LLM_PROVIDER=gemini/' .env
echo "Switched to Cloud LLM (Gemini)."
# Restart if running
./scripts/restart.applescript
