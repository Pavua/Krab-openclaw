#!/bin/bash
cd "$(dirname "$0")"

if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

source venv/bin/activate

echo "🚀 Running Full Test Suite (Idle Mode)..."
echo "Results will be saved to tests/report.html"

# Ensure pytest-html is installed or fallback to simple output
pip install pytest-html -q || true

pytest tests/ --html=tests/report.html --self-contained-html -v

echo "Done! Report saved to tests/report.html"
echo "Press any key to close."
read -n 1
