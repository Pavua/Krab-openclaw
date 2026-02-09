#!/bin/bash
cd "$(dirname "$0")"

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Clean previous
pkill -f "src.main" || true
pkill -f "pure_test" || true

# Run pure test
echo "📦 Installing dotenv if missing..."
pip install python-dotenv > /dev/null 2>&1

echo "🚀 Running Pure Test..."
python scripts/pure_test.py
