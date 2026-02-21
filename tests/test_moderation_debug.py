import pytest
from unittest.mock import MagicMock
from src.core.group_moderation_engine import GroupModerationEngine

def test_get_policy_debug_snapshot():
    """Проверка структуры и данных диагностического снимка."""
    engine = GroupModerationEngine(policy_path=MagicMock())
    engine._store = {"chats": {"123": {"template_name": "strict", "max_links": 5}}}
    
    snapshot = engine.get_policy_debug_snapshot(123)
    
    assert snapshot["chat_id"] == 123
    assert snapshot["template"] == "strict"
    assert "effective_policy" in snapshot
    assert snapshot["engine_version"] == "v3.2-r7"
    assert snapshot["effective_policy"]["max_links"] == 5
