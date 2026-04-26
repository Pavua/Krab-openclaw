# -*- coding: utf-8 -*-
"""
Тесты LLMIntentClassifier (Smart Routing Phase 2).

Все LM Studio calls замоканы через monkeypatch httpx.AsyncClient.post.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.chat_response_policy import ChatMode, ChatResponsePolicy
from src.core.llm_intent_classifier import (
    CACHE_MAX_SIZE,
    ChatMessage,
    IntentResult,
    LLMIntentClassifier,
    get_classifier,
    reset_classifier,
)

# --- Helpers ---------------------------------------------------------------


def _mk_policy(
    mode: ChatMode = ChatMode.NORMAL, blocked: list[str] | None = None
) -> ChatResponsePolicy:
    return ChatResponsePolicy(chat_id="-100123", mode=mode, blocked_topics=blocked or [])


def _mk_msg(
    text: str, sender_id: int = 42, name: str = "alice", is_krab: bool = False
) -> ChatMessage:
    return ChatMessage(
        sender_name=name, sender_id=sender_id, text=text, timestamp=time.time(), is_krab=is_krab
    )


def _mk_lm_response(
    should_respond: bool, confidence: float, reasoning: str = "ok", *, wrap_md: bool = False
):
    """Build a fake httpx.Response for the LM Studio JSON body."""
    body = json.dumps(
        {"should_respond": should_respond, "confidence": confidence, "reasoning": reasoning},
        ensure_ascii=False,
    )
    if wrap_md:
        body = "```json\n" + body + "\n```"
    payload = {"choices": [{"message": {"content": body}}]}
    resp = MagicMock(spec=httpx.Response)
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _patch_post(monkeypatch, response_or_exc):
    """Patch httpx.AsyncClient.post — async function returning response or raising."""

    async def fake_post(self, url, **kwargs):
        if isinstance(response_or_exc, Exception):
            raise response_or_exc
        return response_or_exc

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


# --- Dataclasses -----------------------------------------------------------


def test_chat_message_dataclass():
    m = ChatMessage(sender_name="bob", sender_id=1, text="hi", timestamp=123.0)
    assert m.sender_name == "bob"
    assert m.is_krab is False


def test_intent_result_dataclass_defaults():
    r = IntentResult(should_respond=True, confidence=0.9, reasoning="x")
    assert r.cached is False
    assert r.latency_ms == 0.0
    assert r.error is None


# --- Cache key -------------------------------------------------------------


def test_cache_key_same_input_same_key():
    ctx = [_mk_msg("hi")]
    k1 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-1", ChatMode.NORMAL)
    k2 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-1", ChatMode.NORMAL)
    assert k1 == k2


def test_cache_key_different_chat_id():
    ctx = [_mk_msg("hi")]
    k1 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-1", ChatMode.NORMAL)
    k2 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-2", ChatMode.NORMAL)
    assert k1 != k2


def test_cache_key_different_mode():
    ctx = [_mk_msg("hi")]
    k1 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-1", ChatMode.NORMAL)
    k2 = LLMIntentClassifier._make_cache_key("hello", ctx, "chat-1", ChatMode.CHATTY)
    assert k1 != k2


# --- Prompt building -------------------------------------------------------


def test_prompt_contains_mode_and_threshold():
    policy = _mk_policy(ChatMode.CAUTIOUS)
    prompt = LLMIntentClassifier._build_prompt("привет", [_mk_msg("ctx1")], policy)
    assert "cautious" in prompt
    assert "threshold=" in prompt
    assert "привет" in prompt
    assert "ctx1" in prompt


def test_prompt_silent_hint():
    policy = _mk_policy(ChatMode.SILENT)
    prompt = LLMIntentClassifier._build_prompt("hi", [], policy)
    assert "НИКОГДА" in prompt


def test_prompt_chatty_hint():
    policy = _mk_policy(ChatMode.CHATTY)
    prompt = LLMIntentClassifier._build_prompt("hi", [], policy)
    assert "активный" in prompt


def test_prompt_blocked_topics():
    policy = _mk_policy(blocked=["crypto", "politics"])
    prompt = LLMIntentClassifier._build_prompt("hi", [], policy)
    assert "crypto" in prompt
    assert "politics" in prompt


def test_prompt_empty_context():
    prompt = LLMIntentClassifier._build_prompt("hi", [], _mk_policy())
    assert "(пусто)" in prompt


def test_prompt_truncates_long_messages():
    long = "x" * 500
    prompt = LLMIntentClassifier._build_prompt("test", [_mk_msg(long)], _mk_policy())
    # должно быть сокращено до 200 + "..."
    assert "x" * 500 not in prompt
    assert "..." in prompt


def test_prompt_uses_last_7_messages():
    msgs = [_mk_msg(f"msg-{i}") for i in range(20)]
    prompt = LLMIntentClassifier._build_prompt("test", msgs, _mk_policy())
    assert "msg-19" in prompt
    assert "msg-13" in prompt
    assert "msg-12" not in prompt


def test_prompt_marks_krab_messages():
    msgs = [_mk_msg("hello я Krab", is_krab=True)]
    prompt = LLMIntentClassifier._build_prompt("test", msgs, _mk_policy())
    assert "[Krab]" in prompt


# --- classify_intent_for_krab ---------------------------------------------


@pytest.mark.asyncio
async def test_classify_yes_response(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(True, 0.85, "явное обращение"))
    res = await clf.classify_intent_for_krab("Krab, что думаешь?", [], "chat-1", _mk_policy())
    assert res.should_respond is True
    assert res.confidence == 0.85
    assert res.cached is False
    assert res.error is None
    assert res.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_classify_no_response(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(False, 0.2, "off-topic"))
    res = await clf.classify_intent_for_krab("эй вася привет", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.confidence == 0.2


@pytest.mark.asyncio
async def test_classify_cache_hit(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(True, 0.9))
    r1 = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert r1.cached is False

    # Второй вызов — даже если LM сломан, должен вернуть cached.
    _patch_post(monkeypatch, RuntimeError("must not be called"))
    r2 = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert r2.cached is True
    assert r2.should_respond is True
    assert r2.confidence == 0.9


@pytest.mark.asyncio
async def test_classify_cache_expires_after_ttl(monkeypatch):
    clf = LLMIntentClassifier(cache_ttl_sec=0.05)
    _patch_post(monkeypatch, _mk_lm_response(True, 0.9))
    await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    await asyncio.sleep(0.1)
    _patch_post(monkeypatch, _mk_lm_response(False, 0.1))
    r = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert r.cached is False
    assert r.should_respond is False  # свежий ответ


@pytest.mark.asyncio
async def test_classify_lru_eviction(monkeypatch):
    clf = LLMIntentClassifier(cache_max_size=3)
    _patch_post(monkeypatch, _mk_lm_response(True, 0.9))
    for i in range(5):
        await clf.classify_intent_for_krab(f"msg-{i}", [], "chat-1", _mk_policy())
    assert clf.cache_size() == 3


@pytest.mark.asyncio
async def test_classify_llm_http_error(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, httpx.ConnectError("LM Studio down"))
    res = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.confidence == 0.0
    assert res.error is not None
    assert "ConnectError" in res.reasoning


@pytest.mark.asyncio
async def test_classify_timeout(monkeypatch):
    clf = LLMIntentClassifier(timeout=0.1)
    _patch_post(monkeypatch, httpx.TimeoutException("timed out"))
    res = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.error is not None


@pytest.mark.asyncio
async def test_classify_empty_text():
    clf = LLMIntentClassifier()
    res = await clf.classify_intent_for_krab("", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.error == "empty"


@pytest.mark.asyncio
async def test_classify_whitespace_only():
    clf = LLMIntentClassifier()
    res = await clf.classify_intent_for_krab("   \n  ", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.error == "empty"


@pytest.mark.asyncio
async def test_classify_markdown_wrapped_json(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(True, 0.7, "ok", wrap_md=True))
    res = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert res.should_respond is True
    assert res.confidence == 0.7


@pytest.mark.asyncio
async def test_classify_invalid_json(monkeypatch):
    clf = LLMIntentClassifier()
    bad = MagicMock(spec=httpx.Response)
    bad.json.return_value = {"choices": [{"message": {"content": "not a json at all"}}]}
    bad.raise_for_status.return_value = None
    _patch_post(monkeypatch, bad)
    res = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert res.should_respond is False
    assert res.error is not None


@pytest.mark.asyncio
async def test_classify_confidence_clamped(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(True, 5.0))  # out-of-range
    res = await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy())
    assert res.confidence == 1.0


@pytest.mark.asyncio
async def test_classify_concurrent_calls_no_corruption(monkeypatch):
    clf = LLMIntentClassifier()
    _patch_post(monkeypatch, _mk_lm_response(True, 0.9))

    async def one(i: int):
        return await clf.classify_intent_for_krab(f"msg-{i}", [], "chat-1", _mk_policy())

    results = await asyncio.gather(*[one(i) for i in range(20)])
    assert all(r.should_respond for r in results)
    # cache корректно содержит все уникальные ключи
    assert clf.cache_size() == 20


@pytest.mark.asyncio
async def test_mode_hint_in_prompt_called(monkeypatch):
    """Verify mode-specific text reaches the LM prompt."""
    clf = LLMIntentClassifier()
    captured: dict = {}

    async def capture_post(self, url, **kwargs):
        captured["prompt"] = kwargs["json"]["messages"][0]["content"]
        return _mk_lm_response(True, 0.5)

    monkeypatch.setattr(httpx.AsyncClient, "post", capture_post)
    await clf.classify_intent_for_krab("hi", [], "chat-1", _mk_policy(ChatMode.CAUTIOUS))
    assert "cautious" in captured["prompt"]
    assert "осторожно" in captured["prompt"]


# --- Singleton -------------------------------------------------------------


def test_get_classifier_singleton():
    reset_classifier()
    a = get_classifier()
    b = get_classifier()
    assert a is b
    reset_classifier()
    c = get_classifier()
    assert c is not a


def test_clear_cache():
    clf = LLMIntentClassifier()
    clf._cache["x"] = (time.time(), IntentResult(True, 0.5, "x"))
    assert clf.cache_size() == 1
    clf.clear_cache()
    assert clf.cache_size() == 0
