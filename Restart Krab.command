#!/bin/bash
# 🔄 Restart Krab 🦀
# Назначение: legacy-restart, сведённый к канонической паре `new Stop` -> `new start`.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🔄 Restarting Krab..."
"$DIR/new Stop Krab.command"
sleep 2
osascript -e "tell application \"Terminal\"
    activate
    do script \"cd '$DIR' && printf '\\\033]2;🦀 KRAB USERBOT\\\007' && ./new\\ start_krab.command\"
end tell"
echo "✅ Restart command sent."
sleep 1
