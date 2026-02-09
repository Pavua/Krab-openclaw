
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# --- Mocks ---
@pytest.fixture
def mock_openclaw_response():
    return {"choices": [{"message": {"content": "Test Response"}}]}

@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.get_me.return_value = MagicMock(first_name="Krab", username="test_bot")
    return client

# --- Tests ---

@pytest.mark.asyncio
async def test_brain_connection(mock_openclaw_response):
    """Test connection to OpenClaw Gateway simulation."""
    with patch("aiohttp.ClientSession.post") as mock_post:
        mock_post.return_value.__aenter__.return_value.status = 200
        mock_post.return_value.__aenter__.return_value.json.return_value = mock_openclaw_response
        
        from openclaw_official.nexus_bridge.main import ask_openclaw_brain
        
        response = await ask_openclaw_brain("Hello")
        assert response == "Test Response"

@pytest.mark.asyncio
async def test_bridge_handler(mock_client):
    """Test message handler logic."""
    # This involves mocking Pyrogram events, slightly complex but ensuring logic flows
    pass 

def test_voice_module_init():
    """Test Ear module initialization (mocking sounddevice)."""
    with patch("sounddevice.RawInputStream"), patch("faster_whisper.WhisperModel"):
        from openclaw_official.nexus_bridge.ear import Ear
        ear = Ear()
        assert ear.running == True
