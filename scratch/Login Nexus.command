#!/bin/bash
cd "$(dirname "$0")"

# Go to correct folder
if [ -d "openclaw_official" ]; then
    cd openclaw_official
fi

# Kill background instances to free up resources/locks
pkill -f "nexus_bridge.py"

echo "🔐 LOGIN TO NEXUS USERBOT"
echo "This must be done ONCE to save your session."
echo "----------------------------------------"
echo "Run the command below (I cannot run it for you because it needs your input):"
echo ""
echo "python3 nexus_bridge.py"
echo ""
echo "----------------------------------------"
echo "1. Enter your phone number (e.g. +1234567890)"
echo "2. Enter the code you receive in Telegram"
echo "3. If asked for password (2FA), enter it."
echo "4. When you see '✅ Userbot Active', press Ctrl+C to exit."
echo "5. Then run 'Start System.command' again."
echo ""
python3 nexus_bridge.py
