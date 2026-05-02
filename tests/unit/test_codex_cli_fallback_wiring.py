# -*- coding: utf-8 -*-
"""
Wave 14-K (Session 33) — wire LLMRetryableError to fallback retry.

Live bug 2026-05-02 22:28: codex-cli first-chunk hang detector raised
LLMRetryableError, but it bubbled past `_finish_ai_request_background`
retry-loop (foreground path went directly through `_run_llm_request_flow`),
hit outer handler in `userbot_bridge._process_message`, and the user
saw "🦀❌ Ошибка: codex-cli first-chunk hang" instead of silent fallback.

Wave 14-K extracts the retry-loop into `_run_llm_request_flow_with_auto_retry`
and uses it from BOTH foreground and background paths. Also adds
`retry_model_override` + `user_progress_notice` to `LLMRetryableError`
so the next attempt forces `openai/gpt-5.5` and shows the contextual
"⏱️ Codex медленно..." progress message.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.userbot.llm_retry import LLMRetryableError


def _make_mixin_instance() -> Any:
    """Builds a thin object with just the methods needed for retry-loop testing.

    We can't instantiate `KraabUserbot` (full Pyrogram client) so we build a
    plain object and bind the helper as a method.
    """
    from src.userbot import llm_flow

    obj = MagicMock()
    obj._run_llm_request_flow_with_auto_retry = (
        llm_flow.LLMFlowMixin._run_llm_request_flow_with_auto_retry.__get__(obj)
    )
    return obj


def test_llm_retryable_error_carries_override_fields():
    """LLMRetryableError новые поля retry_model_override + user_progress_notice."""
    err = LLMRetryableError(
        "codex-cli first-chunk hang",
        "provider_timeout: codex-cli не отдал первый chunk за 45s",
        retry_model_override="openai/gpt-5.5",
        user_progress_notice="⏱️ Codex медленно отвечает (>45s). Переключаюсь на резервную модель...",
    )
    assert err.error_text.startswith("provider_timeout")
    assert err.retry_model_override == "openai/gpt-5.5"
    assert "Codex медленно" in err.user_progress_notice


def test_llm_retryable_error_defaults_no_override():
    """Backward compat: без override полей None."""
    err = LLMRetryableError("x", "y")
    assert err.retry_model_override is None
    assert err.user_progress_notice is None


@pytest.mark.asyncio
async def test_codex_hang_triggers_openai_fallback(monkeypatch):
    """codex hang → retry с preferred_model_override='openai/gpt-5.5'."""
    obj = _make_mixin_instance()

    calls: list[dict[str, Any]] = []

    async def fake_run_flow(**kwargs):
        calls.append(dict(kwargs))
        if len(calls) == 1:
            raise LLMRetryableError(
                "codex-cli first-chunk hang",
                "provider_timeout: codex-cli не отдал первый chunk за 45s",
                retry_model_override="openai/gpt-5.5",
                user_progress_notice="⏱️ Codex медленно...",
            )
        # Second attempt succeeds (openai)
        return None

    obj._run_llm_request_flow = fake_run_flow

    # silence safe_edit
    async def fake_safe_edit(msg, text):
        return msg

    obj._safe_edit = fake_safe_edit

    # zero retry delay for fast test
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_DELAY_SEC", 0.0, raising=False)
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_COUNT", 1, raising=False)

    temp_msg = MagicMock()
    await obj._run_llm_request_flow_with_auto_retry(
        prefer_send_message_for_background=False,
        hard_cap_sec=0.0,
        chat_id="123",
        temp_msg=temp_msg,
        message=None,
        images=[],
    )

    assert len(calls) == 2, f"Expected 2 attempts, got {len(calls)}"
    # First attempt: no override
    assert calls[0].get("preferred_model_override") in (None, "")
    # Second attempt: openai fallback
    assert calls[1].get("preferred_model_override") == "openai/gpt-5.5"


@pytest.mark.asyncio
async def test_user_sees_progress_message(monkeypatch):
    """Verify retry uses user_progress_notice from LLMRetryableError."""
    obj = _make_mixin_instance()

    edited_texts: list[str] = []

    async def fake_safe_edit(msg, text):
        edited_texts.append(text)
        return msg

    obj._safe_edit = fake_safe_edit

    async def fake_run_flow(**kwargs):
        if not edited_texts:
            raise LLMRetryableError(
                "codex-cli first-chunk hang",
                "provider_timeout",
                retry_model_override="openai/gpt-5.5",
                user_progress_notice="⏱️ Codex медленно отвечает (>45s). Переключаюсь...",
            )
        return None

    obj._run_llm_request_flow = fake_run_flow

    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_DELAY_SEC", 0.0, raising=False)
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_COUNT", 1, raising=False)

    temp_msg = MagicMock()
    await obj._run_llm_request_flow_with_auto_retry(
        prefer_send_message_for_background=False,
        hard_cap_sec=0.0,
        chat_id="123",
        temp_msg=temp_msg,
        message=None,
        images=[],
    )
    # User saw the embedded "Codex медленно" notice (not the generic "🔄 Повторная попытка")
    assert any("Codex медленно" in t for t in edited_texts), edited_texts


@pytest.mark.asyncio
async def test_no_retry_if_openai_also_fails(monkeypatch):
    """Both attempts fail → final error notice, no infinite loop."""
    obj = _make_mixin_instance()

    edited_texts: list[str] = []

    async def fake_safe_edit(msg, text):
        edited_texts.append(text)
        return msg

    obj._safe_edit = fake_safe_edit

    call_count = {"n": 0}

    async def fake_run_flow(**kwargs):
        call_count["n"] += 1
        raise LLMRetryableError(
            "second hang",
            "provider_timeout: openai gpt-5.5 also failed",
            retry_model_override="openai/gpt-5.5",
        )

    obj._run_llm_request_flow = fake_run_flow

    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_DELAY_SEC", 0.0, raising=False)
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_COUNT", 1, raising=False)

    temp_msg = MagicMock()
    await obj._run_llm_request_flow_with_auto_retry(
        prefer_send_message_for_background=False,
        hard_cap_sec=0.0,
        chat_id="123",
        temp_msg=temp_msg,
        message=None,
        images=[],
    )
    # Total attempts = 1 initial + 1 retry = 2 (with max_retries=1)
    assert call_count["n"] == 2
    # Final message contains the openai error
    final = edited_texts[-1]
    assert "❌" in final and "openai" in final.lower()


@pytest.mark.asyncio
async def test_successful_fallback_returns_openai_response(monkeypatch):
    """Happy path: codex hang → openai succeeds → no error visible to user."""
    obj = _make_mixin_instance()

    async def fake_safe_edit(msg, text):
        return msg

    obj._safe_edit = fake_safe_edit

    attempt_models: list[Any] = []

    async def fake_run_flow(**kwargs):
        attempt_models.append(kwargs.get("preferred_model_override"))
        if len(attempt_models) == 1:
            raise LLMRetryableError(
                "codex hang",
                "provider_timeout",
                retry_model_override="openai/gpt-5.5",
                user_progress_notice="⏱️ Codex медленно...",
            )
        return None  # success

    obj._run_llm_request_flow = fake_run_flow

    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_DELAY_SEC", 0.0, raising=False)
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_COUNT", 1, raising=False)

    temp_msg = MagicMock()
    # Should NOT raise — silent recovery
    await obj._run_llm_request_flow_with_auto_retry(
        prefer_send_message_for_background=False,
        hard_cap_sec=0.0,
        chat_id="123",
        temp_msg=temp_msg,
        message=None,
        images=[],
    )
    assert attempt_models == [None, "openai/gpt-5.5"]


@pytest.mark.asyncio
async def test_cancelled_error_stagnation_silent_return(monkeypatch):
    """Stagnation cancel → silent return (no retry, no error to user)."""
    from src.userbot.llm_flow import LLM_STAGNATION_CANCEL_REASON

    obj = _make_mixin_instance()

    async def fake_safe_edit(msg, text):
        return msg

    obj._safe_edit = fake_safe_edit

    async def fake_run_flow(**kwargs):
        raise asyncio.CancelledError(LLM_STAGNATION_CANCEL_REASON)

    obj._run_llm_request_flow = fake_run_flow

    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_DELAY_SEC", 0.0, raising=False)
    monkeypatch.setattr("src.userbot.llm_flow.config.OPENCLAW_AUTO_RETRY_COUNT", 1, raising=False)

    temp_msg = MagicMock()
    # Should return silently
    await obj._run_llm_request_flow_with_auto_retry(
        prefer_send_message_for_background=True,
        hard_cap_sec=0.0,
        chat_id="123",
        temp_msg=temp_msg,
        message=None,
        images=[],
    )
