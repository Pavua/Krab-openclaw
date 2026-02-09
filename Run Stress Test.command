#!/bin/bash
# 🧪 Run Stress Test 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🧪 Launching Stress Test in new window..."
osascript -e "tell application \"Terminal\" to do script \"cd '$DIR' && ./scripts/stress_tester.sh\""
