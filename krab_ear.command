#!/bin/bash
# Krab Ear — MacWhisper Analog
cd "$(dirname "$0")"
clear
echo "👂 Krab Ear v1.0 — Desktop Transcription (FILE MODE)"
echo "----------------------------------------"
echo "⚠️  Важно: этот скрипт НЕ запускает backend Krab Ear Agent."
echo "    Каноничный backend-старт: ./start_krab_ear_backend.command"
echo "----------------------------------------"

if [ -z "$1" ]; then
    echo "💡 Drag and drop an audio file onto this window and press Enter,"
    echo "   or run: ./krab_ear.command <file_path>"
    read -p "File path: " FILE_PATH
else
    FILE_PATH="$1"
fi

# Remove quotes if dragged/dropped
FILE_PATH=$(echo "$FILE_PATH" | sed 's/^"//;s/"$//;s/^'\''//;s/'\''$//')

if [ ! -f "$FILE_PATH" ]; then
    echo "❌ Error: File not found: $FILE_PATH"
    sleep 3
    exit 1
fi

if [ -d "venv" ]; then
    source venv/bin/activate
fi

PYTHONPATH=. python3 src/utils/voice_bridge.py "$FILE_PATH"

echo "----------------------------------------"
echo "Press any key to exit..."
read -n 1
