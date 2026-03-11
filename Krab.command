#!/bin/bash
# 🦀 Krab Launcher for macOS
# Назначение: legacy-точка входа, перенаправленная на канонический `new start_krab.command`.
# Почему так: старый launcher запускал отдельный bootstrap и мог оставлять полуживой gateway.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

chmod +x *.command 2>/dev/null
chmod +x scripts/*.command 2>/dev/null

osascript -e "tell application \"Terminal\"
    activate
    do script \"cd '$DIR' && printf '\\\033]2;🦀 KRAB USERBOT\\\007' && ./new\\ start_krab.command\"
end tell"
