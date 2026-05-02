"""Bug 14 (Session 32) — outer hard cap on LLM response pipeline.

Tests `_finish_ai_request_background` wraps `_run_llm_request_flow` with
asyncio.wait_for(timeout=KRAB_RESPONSE_HARD_CAP_SEC), and on timeout sends a
fallback message via `_safe_reply_or_send_new`.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src import config as cfg_mod
from src.userbot import llm_flow as llm_flow_mod


class _DummyMixin(llm_flow_mod.LLMFlowMixin):
    """Subclass exposing ONLY hard-cap path; everything else stubbed."""

    def __init__(self) -> None:
        self._safe_reply_or_send_new = AsyncMock()
        self._safe_edit = AsyncMock()


def _kwargs(chat_id: str = "12345"):
    return {
        "message": SimpleNamespace(chat=SimpleNamespace(id=int(chat_id))),
        "temp_msg": None,
        "is_self": False,
        "query": "test",
        "chat_id": chat_id,
        "runtime_chat_id": chat_id,
        "access_profile": None,
        "is_allowed_sender": True,
        "incoming_item_result": None,
        "images": [],
        "force_cloud": False,
        "system_prompt": "",
        "action_stop_event": asyncio.Event(),
        "action_task": None,
        "show_progress_notices": False,
    }


@pytest.mark.asyncio
async def test_hard_cap_fires_on_long_call(monkeypatch):
    """LLM зависает 90s → hard cap (60s default) убивает + fallback отправлен."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.5)
    # codex-cli detection возвращает не-codex модель → 60s/0.5s cap path
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    obj = _DummyMixin()

    async def slow_flow(**_kwargs):
        await asyncio.sleep(90.0)

    with patch.object(obj, "_run_llm_request_flow", side_effect=slow_flow):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_awaited_once()
    sent_text = obj._safe_reply_or_send_new.await_args.args[1]
    assert "слишком долго" in sent_text


@pytest.mark.asyncio
async def test_hard_cap_does_not_fire_on_fast_call(monkeypatch):
    """LLM возвращает за 0.05s → cap не должен сработать, fallback не отправлен."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 1.0)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    obj = _DummyMixin()

    async def fast_flow(**_kwargs):
        await asyncio.sleep(0.05)

    with patch.object(obj, "_run_llm_request_flow", side_effect=fast_flow):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_not_awaited()


@pytest.mark.asyncio
async def test_codex_cli_uses_wall_clock_cap(monkeypatch):
    """codex-cli модель → cap = KRAB_LLM_WALL_CLOCK_CAP_SEC (180s по умолчанию),
    не KRAB_RESPONSE_HARD_CAP_SEC (60s).
    """
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.5)
    monkeypatch.setattr(cfg_mod.Config, "KRAB_LLM_WALL_CLOCK_CAP_SEC", 5.0)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "openai/codex-cli"
    )

    obj = _DummyMixin()
    cap = obj._resolve_response_hard_cap_sec(has_images=False)
    assert cap == 5.0  # codex-cli получил wall-clock cap, а не 0.5

    # Verify non-codex falls back to 0.5
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )
    cap2 = obj._resolve_response_hard_cap_sec(has_images=False)
    assert cap2 == 0.5


@pytest.mark.asyncio
async def test_env_override_raises_cap(monkeypatch):
    """KRAB_RESPONSE_HARD_CAP_SEC=120 raises cap from default 60 to 120."""
    # Симуляция env-override через config attribute (env читается на load).
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 120.0)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    obj = _DummyMixin()
    cap = obj._resolve_response_hard_cap_sec(has_images=False)
    assert cap == 120.0


@pytest.mark.asyncio
async def test_cap_disabled_when_zero(monkeypatch):
    """KRAB_RESPONSE_HARD_CAP_SEC=0 → cap отключён, slow flow not killed."""
    monkeypatch.setattr(cfg_mod.Config, "KRAB_RESPONSE_HARD_CAP_SEC", 0.0)
    monkeypatch.setattr(
        llm_flow_mod, "_current_runtime_primary_model", lambda: "google/gemini-3-pro"
    )

    obj = _DummyMixin()

    call_count = {"n": 0}

    async def quick_flow(**_kwargs):
        call_count["n"] += 1
        await asyncio.sleep(0.05)

    with patch.object(obj, "_run_llm_request_flow", side_effect=quick_flow):
        await obj._finish_ai_request_background(**_kwargs())

    obj._safe_reply_or_send_new.assert_not_awaited()
    assert call_count["n"] == 1
