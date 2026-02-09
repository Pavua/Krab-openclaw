#!/bin/bash
# 🔄 Restart Krab 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🔄 Restarting Krab..."
./Stop\ Krab.command
sleep 1
./Krab.command
echo "✅ Restart command sent."
sleep 1
