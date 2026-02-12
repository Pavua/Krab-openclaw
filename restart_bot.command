#!/bin/bash
cd "$(dirname "$0")"
echo "🔄 Restarting Krab v7.2..."

# Find and kill existing process if running
pkill -f "python src/main.py"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Run in background or foreground? Let's do foreground so user sees logs.
python src/main.py
