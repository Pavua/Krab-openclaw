# -*- coding: utf-8 -*-
"""
Integration tests for Smart Routing Phase 5 — detect_smart_trigger.

Покрывают 5-stage pipeline (hard gates → policy → regex → LLM → fallback)
с реальным ChatResponsePolicyStore (tmp_path) и mocked LLMIntentClassifier.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.chat_response_policy import (
    ChatMode,
    ChatResponsePolicyStore,
)
from src.core.llm_intent_classifier import IntentResult
from src.core.trigger_detector import (
    SmartTriggerResult,
    TriggerType,
    detect_smart_trigger,
)


@pytest.fixture
def policy_store(tmp_path):
    return ChatResponsePolicyStore(path=tmp_path / "policies.json")


@pytest.fixture
def mock_classifier():
    classifier = MagicMock()
    classifier.classify_intent_for_krab = AsyncMock()
    return classifier


@pytest.mark.asyncio
async def test_stage1_hard_gate_command(policy_store):
    result = await detect_smart_trigger(
        text="!swarm coders fix bug",
        chat_id="42",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=True,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=None,
    )
    assert isinstance(result, SmartTriggerResult)
    assert result.should_respond is True
    assert result.decision_path == "hard_gate"
    assert result.confidence == 1.0


@pytest.mark.asyncio
async def test_stage1_hard_gate_explicit_mention(policy_store):
    result = await detect_smart_trigger(
        text="Краб, как дела?",
        chat_id="42",
        is_reply_to_me=False,
        has_explicit_mention=True,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
    )
    assert result.should_respond is True
    assert result.decision_path == "hard_gate"


@pytest.mark.asyncio
async def test_stage1_hard_gate_reply_to_me(policy_store):
    result = await detect_smart_trigger(
        text="спасибо",
        chat_id="42",
        is_reply_to_me=True,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
    )
    assert result.should_respond is True
    assert result.decision_path == "hard_gate"


@pytest.mark.asyncio
async def test_stage2_policy_silent_drops(policy_store):
    policy_store.update_policy("99", mode=ChatMode.SILENT)
    result = await detect_smart_trigger(
        text="кто-то знает как это сделать?",
        chat_id="99",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
    )
    assert result.should_respond is False
    assert result.decision_path == "policy_silent"


@pytest.mark.asyncio
async def test_stage3_regex_high_score_fires(policy_store):
    """followup window → score 0.65 → regex_high path."""
    from src.core.trigger_detector import last_krab_msg

    last_krab_msg.record("33")
    try:
        result = await detect_smart_trigger(
            text="а ещё подскажи",
            chat_id="33",
            is_reply_to_me=False,
            has_explicit_mention=False,
            has_command=False,
            chat_context=[],
            policy_store=policy_store,
        )
        assert result.should_respond is True
        assert result.decision_path == "regex_high"
        assert result.confidence >= 0.6
        assert result.legacy_result is not None
        assert result.legacy_result.trigger_type == TriggerType.FOLLOWUP_TO_KRAB
    finally:
        last_krab_msg._store.pop("33", None)


@pytest.mark.asyncio
async def test_stage3_regex_low_drops_without_llm(policy_store, mock_classifier):
    """Случайное сообщение без триггеров → regex_low → drop, LLM не вызван."""
    result = await detect_smart_trigger(
        text="просто хорошая погода сегодня",
        chat_id="44",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.should_respond is False
    assert result.decision_path == "regex_low"
    mock_classifier.classify_intent_for_krab.assert_not_called()


@pytest.mark.asyncio
async def test_stage4_llm_yes_with_high_confidence(policy_store, mock_classifier):
    """Borderline regex (0.4) → LLM yes с confidence 0.8 ≥ threshold 0.5 → respond."""
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(
            should_respond=True,
            confidence=0.8,
            reasoning="explicit question to AI",
        )
    )
    result = await detect_smart_trigger(
        text="кто знает почему так?",  # implicit_question score=0.4
        chat_id="55",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    # NORMAL mode threshold = 0.5, confidence 0.8 → respond
    assert result.should_respond is True
    assert result.decision_path == "llm_yes"
    assert result.confidence == 0.8
    mock_classifier.classify_intent_for_krab.assert_awaited_once()


@pytest.mark.asyncio
async def test_stage4_llm_no_drops(policy_store, mock_classifier):
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(
            should_respond=False,
            confidence=0.9,
            reasoning="conversation between others",
        )
    )
    result = await detect_smart_trigger(
        text="посоветуйте плиз",
        chat_id="66",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.should_respond is False
    assert result.decision_path == "llm_no"


@pytest.mark.asyncio
async def test_stage4_llm_error_fallback_to_regex_threshold(policy_store, mock_classifier):
    """LLM exception → llm_error_fallback → regex score vs threshold."""
    mock_classifier.classify_intent_for_krab = AsyncMock(
        side_effect=RuntimeError("LM Studio down")
    )
    result = await detect_smart_trigger(
        text="кто-то знает как починить?",  # score 0.4
        chat_id="77",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    # NORMAL threshold = 0.5, regex 0.4 → drop
    assert result.should_respond is False
    assert result.decision_path == "llm_error_fallback"


@pytest.mark.asyncio
async def test_stage4_llm_intent_error_field_fallback(policy_store, mock_classifier):
    """IntentResult с error → fallback path."""
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(
            should_respond=False,
            confidence=0.0,
            reasoning="",
            error="lm_timeout",
        )
    )
    result = await detect_smart_trigger(
        text="кто шарит?",  # score 0.4
        chat_id="88",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.decision_path == "llm_error_fallback"
    assert result.intent_result is not None


@pytest.mark.asyncio
async def test_per_chat_threshold_cautious_blocks_borderline(policy_store, mock_classifier):
    """CAUTIOUS (threshold 0.7) — LLM yes confidence 0.6 → drop."""
    policy_store.update_policy("111", mode=ChatMode.CAUTIOUS)
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(should_respond=True, confidence=0.6, reasoning="maybe")
    )
    result = await detect_smart_trigger(
        text="кто знает?",  # 0.4 borderline
        chat_id="111",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.decision_path == "llm_yes"
    # CAUTIOUS threshold 0.7 > 0.6 → drop
    assert result.should_respond is False


@pytest.mark.asyncio
async def test_per_chat_threshold_chatty_allows_borderline(policy_store, mock_classifier):
    """CHATTY (threshold 0.3) — LLM yes confidence 0.4 → respond."""
    policy_store.update_policy("222", mode=ChatMode.CHATTY)
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(should_respond=True, confidence=0.4, reasoning="ok")
    )
    result = await detect_smart_trigger(
        text="посоветуйте",
        chat_id="222",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.decision_path == "llm_yes"
    assert result.should_respond is True


@pytest.mark.asyncio
async def test_no_llm_classifier_uses_regex_threshold_fallback(policy_store):
    """llm_classifier=None → regex_threshold_fallback (no LLM call)."""
    result = await detect_smart_trigger(
        text="кто знает где документация?",  # 0.4 borderline
        chat_id="333",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=None,
    )
    assert result.decision_path == "regex_threshold_fallback"
    # NORMAL threshold 0.5 > 0.4 → drop
    assert result.should_respond is False


@pytest.mark.asyncio
async def test_empty_chat_context_works(policy_store, mock_classifier):
    """Empty chat_context — LLM всё равно вызывается, не падает."""
    mock_classifier.classify_intent_for_krab = AsyncMock(
        return_value=IntentResult(should_respond=True, confidence=0.7, reasoning="ok")
    )
    result = await detect_smart_trigger(
        text="кто знает почему?",
        chat_id="444",
        is_reply_to_me=False,
        has_explicit_mention=False,
        has_command=False,
        chat_context=[],
        policy_store=policy_store,
        llm_classifier=mock_classifier,
    )
    assert result.should_respond is True
    assert result.decision_path == "llm_yes"
    args, kwargs = mock_classifier.classify_intent_for_krab.call_args
    assert kwargs.get("chat_context") == []
