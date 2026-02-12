#!/bin/bash
# God Mode Launcher (Native)
# v1.0
cd "$(dirname "$0")"

echo "🌌 Starting Krab in GOD MODE (Native macOS)..."
echo "ℹ️  This mode grants full access to your system (Files, Apps, Scripts, Browser)."
echo "ℹ️  Dashboard: http://localhost:8080"

# Check Python environment
if [ -d ".venv" ]; then
    PYTHON=".venv/bin/python3"
else
    echo "⚠️  Virtual environment not found. Using system python3."
    PYTHON="python3"
fi

# Run
$PYTHON src/main.py
