#!/bin/bash
# Валидация Krab v5.2 Omni-Presence

echo "🛠 Verifying Omni-Presence Components..."

# 1. MCP Import Check
echo "Test 1: MCP Libraries..."
./.venv/bin/python3 -c "import mcp; import structlog; print('✅ MCP OK')" || exit 1

# 2. Vision Check
echo "Test 2: ScreenCatcher..."
./.venv/bin/python3 -c "from src.modules.screen_catcher import ScreenCatcher; print('✅ ScreenCatcher Import OK')" || exit 1

# 3. MSS & PyAlert Import Check
echo "Test 3: GUI Libraries..."
./.venv/bin/python3 -c "import mss; import PIL; print('✅ GUI Libs OK')" || exit 1

# 4. Run Pytest for Vision
echo "Test 4: Running Vision Tests..."
./.venv/bin/pytest tests/test_vision.py || exit 1

echo "✅ ALL OMNI-PRESENCE CHECKS PASSED!"
