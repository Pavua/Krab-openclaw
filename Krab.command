#!/bin/bash
# 🦀 Krab Launcher for macOS

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

chmod +x *.command 2>/dev/null
chmod +x *.sh 2>/dev/null
chmod +x scripts/*.command 2>/dev/null

osascript -e "tell application \"Terminal\" 
    activate
    do script \"cd '$DIR' && printf '\\\033]2;🦀 KRAB USERBOT\\\007' && ./run_krab.sh\"
end tell"
