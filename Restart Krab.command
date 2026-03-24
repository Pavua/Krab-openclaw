#!/bin/bash
# 🔄 Restart Krab 🦀
# Назначение: legacy-restart, сведённый к канонической паре `new Stop` -> `new start`.

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ROOT_DIR="$(cd "$DIR/.." && pwd)"
cd "$DIR"

echo "🔄 Restarting Krab..."
"$ROOT_DIR/new Stop Krab.command"
sleep 2
open -a Terminal "$ROOT_DIR/new start_krab.command"
echo "✅ Restart command sent."
sleep 1
