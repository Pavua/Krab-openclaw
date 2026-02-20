# -*- coding: utf-8 -*-
import pytest
from src.core.group_moderation_engine import GroupModerationEngine

def test_moderation_explain_structure():
    """Проверка структуры explain пакета в решении."""
    engine = GroupModerationEngine(policy_path="tmp_policy.json")
    # Подменяем get_policy для теста
    engine.get_policy = lambda cid: {
        "block_links": True,
        "max_links": 0,
        "dry_run": True,
        "actions": {"link": "delete"}
    }
    
    # Симулируем сообщение со ссылкой
    policy, violations, _ = engine._evaluate_non_ai(123, "Check this https://google.com", entities=[{"type": "url"}])
    decision = engine._build_decision(policy, violations)
    
    assert decision["matched"] is True
    assert "explain" in decision
    explain = decision["explain"]
    assert explain["primary_rule"] == "link"
    assert "link" in explain["matched_rules"]
    assert explain["source"] == "AutoMod"
    assert explain["dry_run_reason"] == "Enabled in policy"

def test_moderation_no_violations_explain():
    """Проверка explain пакета при отсутствии нарушений."""
    engine = GroupModerationEngine(policy_path="tmp_policy.json")
    engine.get_policy = lambda cid: {"dry_run": True}
    
    policy, violations, _ = engine._evaluate_non_ai(123, "Hello world", entities=[])
    decision = engine._build_decision(policy, violations)
    
    assert decision["matched"] is False
    assert decision["explain"]["matched_rules"] == []
    assert decision["explain"]["dry_run_reason"] == "No violations"
