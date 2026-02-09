#!/bin/bash
cd "$(dirname "$0")"

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Clean previous
pkill -f "src.main" || true

# Run simple test
python scripts/simple_run.py
