import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.handlers.ai import _process_auto_reply
from pyrogram import filters

@pytest.mark.asyncio
async def test_auto_reply_photo_vision():
    """Test that photo triggers OpenClaw Vision analysis."""
    # Mock Client & Message
    client = AsyncMock()
    client.get_me.return_value.username = "KrabBot"
    
    # Message should be MagicMock because attributes are accessed synchronously
    message = MagicMock()
    message.chat.type.name = "PRIVATE"
    message.chat.id = 123
    message.from_user.username = "tester"
    message.from_user.id = 999
    message.text = None
    message.caption = "Look at this!"
    message.photo = True # Simulate photo presence
    message.voice = None
    
    # Mock download which is awaited
    message.download = AsyncMock(return_value="/tmp/photo.jpg")
    
    # Mock reply_text which is awaited
    msg_mock = AsyncMock()
    message.reply_text = AsyncMock(return_value=msg_mock)
    
    # Mock OpenClaw
    mock_openclaw = AsyncMock()
    mock_openclaw.analyze_image.return_value = {"description": "A cute cat"}
    
    # Mock deps
    mock_memory = MagicMock()
    # sync_telegram_history is awaited
    mock_memory.sync_telegram_history = AsyncMock(return_value=True)
    
    # Router must be MagicMock to support async generator side_effect properly
    mock_router = MagicMock()
    async def stream_gen(*args, **kwargs):
        yield "Oh, what a nice cat!"
    mock_router.route_query_stream.side_effect = stream_gen
    
    deps = {
        "security": MagicMock(),
        "rate_limiter": MagicMock(),
        "memory": mock_memory,
        "router": mock_router,
        "openclaw_client": mock_openclaw,
        "summarizer": None
    }
    deps["security"].get_user_role.return_value = "user"
    deps["rate_limiter"].is_allowed.return_value = True

    # Execute
    await _process_auto_reply(client, message, deps)
    
    # Verify
    mock_openclaw.analyze_image.assert_called_once_with("/tmp/photo.jpg")
    # Verify context injection
    # We expect multiple calls (user msg, bot msg). The first one should contain the vision analysis.
    user_call = mock_memory.save_message.call_args_list[0]
    args, _ = user_call
    saved_content = args[1]["text"]
    assert "[VISION ANALYSIS]" in saved_content
    assert "A cute cat" in saved_content

@pytest.mark.asyncio
async def test_auto_reply_voice_processing():
    """Test that voice triggers OpenClaw STT -> TTS flow."""
    client = AsyncMock()
    client.get_me.return_value.username = "KrabBot"
    
    message = MagicMock()
    message.chat.type.name = "PRIVATE"
    message.chat.id = 123
    message.from_user.username = "tester"
    message.text = None
    message.caption = None
    message.voice = True
    message.photo = None
    
    message.download = AsyncMock(return_value="/tmp/voice.ogg")
    message.reply_text = AsyncMock(return_value=AsyncMock())
    message.reply_voice = AsyncMock()
    
    mock_openclaw = AsyncMock()
    mock_openclaw.transcribe_audio.return_value = {"text": "Hello Krab"}
    mock_openclaw.generate_speech.return_value = "/tmp/reply.ogg"
    
    mock_memory = MagicMock()
    mock_memory.sync_telegram_history = AsyncMock()
    
    mock_router = MagicMock()
    async def stream_gen(*args, **kwargs):
        yield "Hello user!"
    mock_router.route_query_stream.side_effect = stream_gen
    
    deps = {
        "security": MagicMock(),
        "rate_limiter": MagicMock(),
        "memory": mock_memory,
        "router": mock_router,
        "openclaw_client": mock_openclaw,
        "summarizer": None
    }
    deps["security"].get_user_role.return_value = "user"
    deps["rate_limiter"].is_allowed.return_value = True
    
    with patch("os.path.exists", return_value=True), \
         patch("os.remove"):
        
        await _process_auto_reply(client, message, deps)
        
        # Verify STT
        mock_openclaw.transcribe_audio.assert_called_once_with("/tmp/voice.ogg")
        
        # Verify TTS generation was requested
        mock_openclaw.generate_speech.assert_called_once_with("Hello user!")
        
        # Verify reply_voice was called
        message.reply_voice.assert_called_once()
