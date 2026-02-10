# -*- coding: utf-8 -*-
from src.modules.screen_catcher import ScreenCatcher
from unittest.mock import MagicMock, AsyncMock
import pytest
import os

class MockPerceptor:
    async def analyze_visual(self, path, query):
        if "fail" in path:
            raise Exception("Vision failed")
        return "Analysis result"

@pytest.mark.asyncio
async def test_screen_catcher():
    perceptor = MockPerceptor()
    catcher = ScreenCatcher(perceptor)
    
    # На реальном Mac скриншот может сработать
    real_screen = catcher.capture_screen()
    if real_screen:
        assert os.path.exists(real_screen)
        os.remove(real_screen)

    # Test analyze logic with fake path
    # Create dummy file
    with open("temp/screens/dummy.jpg", "w") as f:
        f.write("test")
        
    try:
        # Puts dummy path
        catcher.capture_screen = MagicMock(return_value="temp/screens/dummy.jpg")
        
        result = await catcher.analyze_screen("test query")
        assert "Analysis result" in result
        assert "👀" in result
        
        # Check cleanup
        assert not os.path.exists("temp/screens/dummy.jpg")
        
    finally:
        if os.path.exists("temp/screens/dummy.jpg"):
            os.remove("temp/screens/dummy.jpg")
