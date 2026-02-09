#!/bin/bash
source $HOME/.zshrc
export PATH="/usr/local/bin:$PATH"

PROJECT_ROOT="/Users/pablito/.gemini/antigravity/scratch"
cd "$PROJECT_ROOT"

PYTHON_EXEC="$PROJECT_ROOT/openclaw_official/nexus_bridge/venv/bin/python3"

echo "🔐 Nexus Bridge Authentication"
echo "----------------------------"
echo "We reset the database to fix the crash. You need to log in again."
echo "Please enter your phone number when prompted (e.g. +1234567890) and the code you receive in Telegram."
echo "----------------------------"

$PYTHON_EXEC openclaw_official/nexus_bridge/main.py
