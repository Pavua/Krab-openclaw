import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.handlers.ai import _process_auto_reply
from src.core.model_manager import ModelRouter
from pyrogram import enums

async def test_ai_handler():
    print("🧪 Testing AI Handler Logic...")
    
    # Mocks
    client = AsyncMock()
    client.get_me.return_value.username = "KrabBot"
    
    security = MagicMock()
    security.get_user_role.return_value = "user" # Not blocked, not stealth
    
    rate_limiter = MagicMock()
    rate_limiter.is_allowed.return_value = True
    
    memory = MagicMock()
    memory.sync_telegram_history = AsyncMock()
    memory.get_recent_context.return_value = []
    
    router = MagicMock()
    router.route_query_stream.return_value = __aiter__(["Hello", " World"])
    
    perceptor = MagicMock()
    perceptor.analyze_image = AsyncMock(return_value="A cute cat")
    perceptor.transcribe = AsyncMock(return_value="Hello Krab")
    perceptor.speak = AsyncMock(return_value="path/to/voice.ogg")
    
    config = MagicMock()
    config.get.side_effect = lambda k, d=None: True if k == "group_chat.allow_replies" else d
    
    deps = {
        "security": security,
        "rate_limiter": rate_limiter,
        "memory": memory,
        "router": router,
        "perceptor": perceptor,
        "config_manager": config,
        "openclaw_client": None, # Should not be used
        "summarizer": None
    }
    
    # 1. Test PM (Should reply)
    print("  - Case 1: Private Message")
    msg_pm = AsyncMock()
    msg_pm.chat.type = enums.ChatType.PRIVATE
    msg_pm.text = "Hello"
    msg_pm.from_user.username = "User"
    msg_pm.from_user.id = 123
    msg_pm.reply_to_message = None
    msg_pm.photo = None
    msg_pm.voice = None
    
    await _process_auto_reply(client, msg_pm, deps)
    msg_pm.reply_text.assert_called()
    print("    ✅ PM replied.")

    # 2. Test Group (No mention, No reply) -> Should IGNORE
    print("  - Case 2: Group (Ignored)")
    msg_group = AsyncMock()
    msg_group.chat.type = enums.ChatType.SUPERGROUP
    msg_group.text = "Just talking"
    msg_group.from_user.username = "User"
    msg_group.reply_to_message = None
    msg_group.photo = None
    
    msg_group.reply_text.reset_mock()
    memory.save_message.reset_mock()
    
    await _process_auto_reply(client, msg_group, deps)
    if not msg_group.reply_text.called and memory.save_message.called:
        print("    ✅ Group ignored (passive saved).")
    else:
        print(f"    ❌ Group logic failed. Called: {msg_group.reply_text.called}")

    # 3. Test Group Reply (Should reply due to config=True)
    print("  - Case 3: Group Reply (Allowed)")
    msg_reply = AsyncMock()
    msg_reply.chat.type = enums.ChatType.SUPERGROUP
    msg_reply.text = "Reply to bot"
    msg_reply.reply_to_message.from_user.is_self = True
    msg_reply.photo = None
    
    msg_reply.reply_text.reset_mock()
    await _process_auto_reply(client, msg_reply, deps)
    if msg_reply.reply_text.called:
        print("    ✅ Group reply handled.")
    else:
        print("    ❌ Group reply ignored.")

    # 4. Test Vision (Photo)
    print("  - Case 4: Vision (Photo)")
    msg_photo = AsyncMock()
    msg_photo.chat.type = enums.ChatType.PRIVATE
    msg_photo.text = None
    msg_photo.caption = "Look at this"
    msg_photo.photo = True
    msg_photo.download = AsyncMock(return_value="photo.jpg")
    
    perceptor.analyze_image.reset_mock()
    msg_photo.reply_text.reset_mock()
    
    await _process_auto_reply(client, msg_photo, deps)
    
    if perceptor.analyze_image.called:
        print("    ✅ Perceptor.analyze_image called.")
    else:
        print("    ❌ Perceptor NOT called.")

async def __aiter__(iterable):
    for item in iterable:
        yield item

async def test_model_manager():
    print("\n🧪 Testing Model Manager Load...")
    
    # We need to patch aiohttp
    with patch("aiohttp.ClientSession.post") as mock_post:
        # Mock Response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_post.return_value.__aenter__.return_value = mock_resp
        
        router = ModelRouter({"LM_STUDIO_URL": "http://localhost:1234/v1"})
        
        # Checking logic
        res = await router.load_local_model("test-model")
        
        if res and mock_post.called:
            print("    ✅ load_local_model tried HTTP.")
            args, kwargs = mock_post.call_args
            print(f"       URL: {args[0]}")
            print(f"       Payload: {kwargs['json']}")
        else:
             print("    ❌ load_local_model failed or didn't use HTTP.")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(test_ai_handler())
    loop.run_until_complete(test_model_manager())
