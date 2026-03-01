#!/bin/bash
# 🧪 Run Tests 🦀

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "🧪 Launching Tests..."

if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
fi

echo "--- Unit Tests ---"
python -m pytest tests/unit/ -v

echo ""
echo "--- Integration Tests ---"
python -m pytest tests/integration/ -v

echo ""
echo "--- E2E Tests ---"
python -m pytest tests/e2e/ -v

echo ""
echo "✅ All tests completed."
read -p "Press Enter to close..."
