#!/bin/bash
cd "$(dirname "$0")/.."
# Switch to local
sed -i '' 's/LLM_PROVIDER=gemini/LLM_PROVIDER=local/' .env
echo "Switched to Local LLM (LM Studio)."
# Restart if running
./scripts/restart.applescript
