#!/bin/bash
# 🔄 Restart Krab 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🔄 Restarting Krab..."
"$DIR/Stop Krab.command"
sleep 2
"$DIR/Krab.command"
echo "✅ Restart command sent."
sleep 1
