
# -*- coding: utf-8 -*-
"""
E2E Scenarios for Group Moderation v2.
Tests handler integration, dry-run toggles, and template application.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram import enums
from src.handlers.groups import register_handlers
from src.core.group_moderation_engine import GroupModerationEngine

# Mocking Pyrogram bits
class MockMessage:
    def __init__(self, text, chat_type=enums.ChatType.SUPERGROUP, user_id=999, is_self=False):
        self.text = text
        self.message_id = 1
        self.chat = MagicMock()
        self.chat.id = -100123
        self.chat.type = chat_type
        self.chat.title = "Test Group"
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.is_self = is_self
        self.from_user.username = "test_user"
        self.entities = None
        self.caption = None
        
        self.reply_text = AsyncMock()
        self.delete = AsyncMock()
        self.edit_text = AsyncMock()
        self.command = text.split() if text else []

class MockClient:
    def __init__(self):
        self.send_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
        self.restrict_chat_member = AsyncMock()
        self.ban_chat_member = AsyncMock()

@pytest.fixture
def engine(tmp_path):
    p = tmp_path / "group_policies.json"
    return GroupModerationEngine(policy_path=str(p), default_dry_run=True)

@pytest.fixture
def deps(engine):
    black_box = MagicMock()
    # Default settings: active, auto_mod ON
    black_box.get_group_settings.return_value = {"is_active": 1, "auto_moderation": 1}
    
    return {
        "black_box": black_box,
        "safe_handler": lambda x: x,
        "group_moderation_engine": engine,
        "reminder_manager": MagicMock(), # needed by some handlers
    }

def get_handlers(deps):
    app = MagicMock()
    handlers = {}
    
    # We need to capture the functions passed to app.on_message
    def mock_on_message(filters=None, group=0):
        def decorator(f):
            handlers[f.__name__] = f
            return f
        return decorator
        
    app.on_message = mock_on_message
    app.on_chat_member_updated = lambda: lambda f: f
    app.on_callback_query = lambda *args, **kwargs: lambda f: f
    
    register_handlers(app, deps)
    return handlers

@pytest.mark.asyncio
async def test_group_template_application(deps):
    """Verify that !group template <name> updates the engine policy."""
    handlers = get_handlers(deps)
    client = MockClient()
    
    with patch("src.handlers.groups.is_owner", return_value=True):
        # 1. Apply strict
        msg_strict = MockMessage("!group template strict")
        await handlers["group_command"](client, msg_strict)
        assert "Шаблон `strict` применен" in msg_strict.reply_text.call_args[0][0]
        
        policy = deps["group_moderation_engine"].get_policy(msg_strict.chat.id)
        assert policy["dry_run"] is False
        assert policy["actions"]["link"] == "ban"

        # 2. Apply lenient
        msg_lenient = MockMessage("!group template lenient")
        await handlers["group_command"](client, msg_lenient)
        assert "Шаблон `lenient` применен" in msg_lenient.reply_text.call_args[0][0]
        
        policy = deps["group_moderation_engine"].get_policy(msg_strict.chat.id)
        assert policy["dry_run"] is True
        assert policy["block_links"] is False

@pytest.mark.asyncio
async def test_automod_lifecycle_dry_run(deps):
    """Verify AutoMod behavior in Dry-Run mode (notifications only)."""
    handlers = get_handlers(deps)
    client = MockClient()
    
    # Message with link
    msg = MockMessage("Hey, check this out: http://example.com/spam", user_id=777)
    
    with patch("src.handlers.groups.is_owner", return_value=False):
        await handlers["auto_mod_handler"](client, msg)
        
        # In dry-run: notification is sent
        client.send_message.assert_called()
        args, kwargs = client.send_message.call_args
        assert "AutoMod DRY-RUN" in args[1]
        
        # Message should NOT be deleted
        assert not msg.delete.called

@pytest.mark.asyncio
async def test_automod_lifecycle_active(deps):
    """Verify AutoMod behavior in Active mode (bans/deletes)."""
    handlers = get_handlers(deps)
    client = MockClient()
    chat_id = -100123
    
    # Switch to strict (dry_run=False, link=ban)
    deps["group_moderation_engine"].apply_template(chat_id, "strict")
    
    # Message with link
    msg = MockMessage("Spam link: http://malware.ru", user_id=888)
    
    with patch("src.handlers.groups.is_owner", return_value=False):
        await handlers["auto_mod_handler"](client, msg)
        
        # 1. Message must be deleted
        msg.delete.assert_called_once()
        
        # 2. User must be banned
        client.ban_chat_member.assert_called_once_with(chat_id, 888)
        
        # 3. Notification sent (non-dry-run version)
        client.send_message.assert_called()
        assert "AutoMod" in client.send_message.call_args[0][1]
        assert "DRY-RUN" not in client.send_message.call_args[0][1]

@pytest.mark.asyncio
async def test_automod_banned_word_mute(deps):
    """Verify banned word triggers mute if configured."""
    handlers = get_handlers(deps)
    client = MockClient()
    chat_id = -100123
    
    # Configure: dry_run=False, banned_word=mute
    deps["group_moderation_engine"].update_policy(chat_id, {
        "dry_run": False,
        "actions": {"banned_word": "mute"},
        "mute_minutes": 10
    })
    deps["group_moderation_engine"].add_banned_word(chat_id, "плохо")
    
    msg = MockMessage("Это очень плохо", user_id=555)
    
    with patch("src.handlers.groups.is_owner", return_value=False):
        await handlers["auto_mod_handler"](client, msg)
        
        # Message deleted
        msg.delete.assert_called_once()
        # User restricted (mute)
        assert client.restrict_chat_member.called
        assert client.restrict_chat_member.call_args[0][1] == 555
