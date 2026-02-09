#!/bin/bash
cd "$(dirname "$0")/../../openclaw_official/ui" || exit

echo "🦀 Starting OpenClaw Official UI..."
# Install deps if needed (check for node_modules)
if [ ! -d "node_modules" ]; then
    echo "📦 Installing UI dependencies..."
    npm install
fi

# Run dev server
echo "🌍 Opening http://localhost:5173"
open http://localhost:5173
npm run dev
