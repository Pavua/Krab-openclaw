
# -*- coding: utf-8 -*-
"""
Тесты для Group Moderation Engine v2 (Phase C/Sprint B).
"""

import pytest
import os
import json
from pathlib import Path
from src.core.group_moderation_engine import GroupModerationEngine

@pytest.fixture
def temp_policy_file(tmp_path):
    p = tmp_path / "test_group_policies.json"
    return str(p)

@pytest.fixture
def engine(temp_policy_file):
    return GroupModerationEngine(policy_path=temp_policy_file, default_dry_run=True)

def test_base_policy(engine):
    policy = engine.get_policy(12345)
    assert policy["dry_run"] is True
    assert policy["actions"]["link"] == "delete"

def test_apply_template_strict(engine):
    chat_id = 111
    engine.apply_template(chat_id, "strict")
    policy = engine.get_policy(chat_id)
    
    assert policy["dry_run"] is False
    assert policy["actions"]["link"] == "ban"
    assert policy["max_links"] == 0
    assert policy["max_caps_ratio"] == 0.40

def test_apply_template_lenient(engine):
    chat_id = 222
    engine.apply_template(chat_id, "lenient")
    policy = engine.get_policy(chat_id)
    
    assert policy["dry_run"] is True
    assert policy["actions"]["link"] == "none"
    assert policy["block_links"] is False

def test_invalid_template(engine):
    with pytest.raises(ValueError, match="не найден"):
        engine.apply_template(123, "non_existent")

def test_evaluate_short_message(engine):
    # Тест на короткое сообщение (не должно триггерить CAPS)
    res = engine.evaluate_message(123, "HELLO")
    assert res["matched"] is False

def test_evaluate_caps_violation(engine):
    # Тест на CAPS
    chat_id = 333
    engine.update_policy(chat_id, {"min_caps_chars": 5, "max_caps_ratio": 0.5})
    res = engine.evaluate_message(chat_id, "VERY LOUD MESSAGE")
    assert res["matched"] is True
    assert res["primary_rule"] == "caps"
    assert res["action"] == "warn" # Default balanced action for caps

def test_evaluate_banned_word(engine):
    chat_id = 444
    engine.add_banned_word(chat_id, "плохоеслово")
    res = engine.evaluate_message(chat_id, "Это очень ПЛОХОЕСЛОВО")
    assert res["matched"] is True
    assert res["primary_rule"] == "banned_word"
    assert "плохоеслово" in res["violations"][0]["meta"]["matches"]

def test_evaluate_links(engine):
    chat_id = 555
    res = engine.evaluate_message(chat_id, "Check this: http://example.com and www.test.com")
    assert res["matched"] is True
    assert res["primary_rule"] == "link"
    assert res["violations"][0]["meta"]["link_count"] == 2

def test_action_priority(engine):
    # Banned word should have priority over caps
    chat_id = 666
    engine.add_banned_word(chat_id, "спам")
    engine.update_policy(chat_id, {"min_caps_chars": 5, "max_caps_ratio": 0.5})
    
    res = engine.evaluate_message(chat_id, "КУПИ ЭТОТ СПАМ СЕЙЧАС")
    assert res["matched"] is True
    assert res["primary_rule"] == "banned_word" # Banned word priority 100 > Caps 50
