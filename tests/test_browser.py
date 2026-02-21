import asyncio
import pytest
import os
from unittest.mock import MagicMock
from src.modules.browser import BrowserAgent

@pytest.mark.asyncio
async def test_browser_agent_lifecycle():
    agent = BrowserAgent(headless=True)
    try:
        await agent.start()
        assert agent.browser is not None
        assert agent.context is not None
        assert agent.page is not None
        
        # Test basic navigation
        # Note: We can't easily mock async playwright without extensive setup,
        # so this is more of an integration test.
        # Use a safe URL like example.com
        res = await agent.browse("https://example.com")
        assert res["title"] is not None
        assert "content" in res
        assert "screenshot_path" in res
        assert os.path.exists(res["screenshot_path"])
        
        # Cleanup screenshot
        os.remove(res["screenshot_path"])
        
    finally:
        await agent.stop()
        assert agent.browser is None

if __name__ == "__main__":
    asyncio.run(test_browser_agent_lifecycle())
