#!/bin/bash
# 🦀 Krab Launcher for macOS

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Делаем скрипты исполняемыми
chmod +x *.command
chmod +x *.sh
chmod +x scripts/*.sh

# Открываем терминал с заголовком и запускаем
osascript -e "tell application \"Terminal\" 
    activate
    do script \"cd '$DIR' && printf '\\\033]2;🦀 KRAB USERBOT\\\007' && ./run_krab.sh\"
end tell"
