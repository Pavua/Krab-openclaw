
# -*- coding: utf-8 -*-
"""
Tests for Provisioning Hardening.
Covers validation rules, flow next-steps, and guardrails.
"""

import pytest
import re
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram import enums
from src.handlers.provisioning import register_handlers
from src.core.provisioning_service import ProvisioningService

class MockMessage:
    def __init__(self, text, user_id=123):
        self.text = text
        self.chat = MagicMock()
        self.chat.id = 123
        self.chat.type = enums.ChatType.PRIVATE # Correct enum
        self.command = text.split() if text else []
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.username = "owner"
        self.reply_text = AsyncMock()

@pytest.fixture
def service(tmp_path):
    return ProvisioningService(
        agents_catalog_path=str(tmp_path / "agents.yaml"),
        skills_catalog_path=str(tmp_path / "skills.yaml"),
        drafts_dir=str(tmp_path / "drafts")
    )

@pytest.fixture
def deps(service):
    return {
        "provisioning_service": service,
        "safe_handler": lambda x: x,
    }

@pytest.mark.asyncio
async def test_provision_draft_validation(deps):
    """Test field validation in create_draft (Sprint Block D)."""
    service = deps["provisioning_service"]
    
    # Valid
    draft = service.create_draft("agent", "my-agent", "coding", "desc", "owner")
    assert draft["name"] == "my-agent"
    
    # Invalid name (spaces) - triggering regex check
    with pytest.raises(ValueError, match="только буквы"):
        service.create_draft("agent", "my agent", "coding", "desc", "owner")

    # Invalid name (symbols)
    with pytest.raises(ValueError, match="только буквы"):
        service.create_draft("agent", "agent!", "coding", "desc", "owner")

@pytest.mark.asyncio
async def test_provision_responses_and_guardrails(deps):
    """Test flow responses and already_applied guardrails."""
    app = MagicMock()
    handlers = {}
    def mock_on_message(filters=None, group=0):
        def decorator(f):
            handlers[f.__name__] = f
            return f
        return decorator
    app.on_message = mock_on_message
    
    from src.handlers.provisioning import register_handlers
    
    with patch("src.handlers.provisioning.is_superuser", return_value=True):
        register_handlers(app, deps)
        handler = handlers["provision_command"]
        
        # 1. Create Draft
        msg1 = MockMessage("!provision draft agent test-alpha coding PowerfulAgent")
        await handler(None, msg1)
        resp1 = msg1.reply_text.call_args[0][0]
        assert "✅ **Draft создан**" in resp1
        draft_id = resp1.split("ID: `")[1].split("`")[0]
        
        # 2. Preview (check next steps)
        msg2 = MockMessage(f"!provision preview {draft_id}")
        await handler(None, msg2)
        resp2 = msg2.reply_text.call_args[0][0]
        assert "🧪 **Preview diff**" in resp2
        assert "Что делать дальше" in resp2
        assert f"!provision apply {draft_id} confirm" in resp2
        
        # 3. Apply first time
        msg3 = MockMessage(f"!provision apply {draft_id} confirm")
        await handler(None, msg3)
        resp3 = msg3.reply_text.call_args[0][0]
        assert "Provisioning apply завершен" in resp3
        assert "Result: `created`" in resp3
        
        # 4. Apply second time (guardrail)
        msg4 = MockMessage(f"!provision apply {draft_id} confirm")
        await handler(None, msg4)
        resp4 = msg4.reply_text.call_args[0][0]
        assert "уже был применен" in resp4
