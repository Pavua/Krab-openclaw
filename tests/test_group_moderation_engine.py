# -*- coding: utf-8 -*-
"""Тесты для GroupModerationEngine (модерация v2)."""

from __future__ import annotations

from pathlib import Path

from src.core.group_moderation_engine import GroupModerationEngine


def _build_engine(tmp_path: Path) -> GroupModerationEngine:
    return GroupModerationEngine(policy_path=str(tmp_path / "group_policies.json"), default_dry_run=True)


def test_default_policy_and_no_match(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    policy = engine.get_policy(-100123)
    assert policy["dry_run"] is True
    assert policy["block_links"] is True

    decision = engine.evaluate_message(-100123, "обычное сообщение без нарушений")
    assert decision["matched"] is False
    assert decision["action"] == "none"


def test_link_rule_detected_and_action_from_policy(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    chat_id = -100321

    decision = engine.evaluate_message(chat_id, "смотри https://example.com")
    assert decision["matched"] is True
    assert decision["primary_rule"] == "link"
    assert decision["action"] == "delete"


def test_banned_word_add_remove(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    chat_id = -100777

    engine.add_banned_word(chat_id, "SCAM")
    decision = engine.evaluate_message(chat_id, "Это scam схема")
    assert decision["matched"] is True
    assert decision["primary_rule"] == "banned_word"

    engine.remove_banned_word(chat_id, "scam")
    decision_after = engine.evaluate_message(chat_id, "Это scam схема")
    assert decision_after["matched"] is False


def test_caps_and_repeated_chars_rules(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path)
    chat_id = -100888

    engine.update_policy(chat_id, {"max_caps_ratio": 0.4, "min_caps_chars": 6, "max_repeated_chars": 5})

    caps_decision = engine.evaluate_message(chat_id, "ЭТО СЛИШКОМ ГРОМКО")
    assert caps_decision["matched"] is True
    assert caps_decision["primary_rule"] in {"caps", "repeated_chars"}

    repeat_decision = engine.evaluate_message(chat_id, "аааааааааааа")
    assert repeat_decision["matched"] is True
    assert repeat_decision["primary_rule"] == "repeated_chars"


def test_policy_persistence_between_instances(tmp_path: Path) -> None:
    chat_id = -100999
    policy_path = tmp_path / "group_policies.json"

    engine1 = GroupModerationEngine(policy_path=str(policy_path), default_dry_run=True)
    engine1.update_policy(chat_id, {"dry_run": False, "actions": {"link": "mute"}})

    engine2 = GroupModerationEngine(policy_path=str(policy_path), default_dry_run=True)
    policy = engine2.get_policy(chat_id)
    assert policy["dry_run"] is False
    assert policy["actions"]["link"] == "mute"
