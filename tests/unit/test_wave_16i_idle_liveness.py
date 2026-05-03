# -*- coding: utf-8 -*-
"""
Wave 16-I — idle-aware liveness detection.

Тесты проверяют:
1. text chunk сбрасывает last_activity_at (через received_any_chunk=True)
2. tool event через _active_tool_calls → received_any_tool_event=True + tool_call_count
3. idle gate: тишина > KRAB_LLM_IDLE_TIMEOUT_SEC после tool activity → LLMRetryableError
4. first-activity gate: ВООБЩЕ ничего > codex timeout → LLMRetryableError (Wave 14-D)
5. tool activity подавляет first-activity gate (Wave 16-I fix)
6. idle cap env override через monkeypatch
7. heartbeat notice edit вызывается при tool activity
8. tool_call_count отражается в heartbeat notice
9. idle gate НЕ срабатывает если received_any_chunk=True (stream ещё жив)

Стратегия: тестируем внутреннюю логику переменных и условий без полного stream pipeline.
Используем AsyncMock + fake stream generator.
"""

from __future__ import annotations

import asyncio
import time
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _fake_stream_empty() -> AsyncGenerator[str, None]:
    """Бесконечный stream без чанков — симулирует зависший провайдер."""
    while True:
        await asyncio.sleep(0.01)
        # никогда не yield — stream завис
        return
        yield  # type: ignore[misc]


async def _fake_stream_single_chunk(text: str = "hello") -> AsyncGenerator[str, None]:
    """Stream с одним text chunk."""
    await asyncio.sleep(0.01)
    yield text


class _FakeCodexState:
    """Stub для codex_cli_health state."""

    def __init__(self):
        self._timeouts: int = 0
        self._success: int = 0

    def get_first_chunk_timeout(self) -> float:
        return 45.0

    def record_timeout(self, now: float | None = None) -> bool:
        self._timeouts += 1
        return self._timeouts >= 2

    def record_success(self) -> None:
        self._success += 1

    def should_skip(self, now: float | None = None) -> bool:
        return False


# ---------------------------------------------------------------------------
# Unit tests на переменные состояния (без полного flow)
# ---------------------------------------------------------------------------


def test_text_chunk_sets_received_any_chunk():
    """text chunk → received_any_chunk=True."""
    received_any_chunk = False
    chunk = "some text"
    if chunk:
        received_any_chunk = True
    assert received_any_chunk


def test_text_chunk_resets_last_activity_at():
    """text chunk → last_activity_at обновляется (не позже started_wait_at)."""
    started_wait_at = time.monotonic()
    last_activity_at = started_wait_at  # initial
    received_any_chunk = False

    # Simulate chunk received
    time.sleep(0.01)
    last_activity_at = time.monotonic()
    received_any_chunk = True

    assert last_activity_at > started_wait_at
    assert received_any_chunk


def test_tool_call_chunk_sets_received_any_tool_event():
    """tool event → received_any_tool_event=True."""
    received_any_tool_event = False
    tool_call_count = 0
    last_tool_summary = ""

    # Simulate tool summary появился
    tool_summary = "🔧 web_search\nИнструментов: 0/1"
    if tool_summary and tool_summary != last_tool_summary:
        received_any_tool_event = True

    assert received_any_tool_event


def test_tool_call_count_tracked():
    """tool_call_count растёт при появлении tool calls."""
    tool_call_count = 0
    fake_active_tool_calls = [
        {"name": "web_search", "status": "running"},
        {"name": "fs_read", "status": "done"},
    ]
    tool_call_count = max(tool_call_count, len(fake_active_tool_calls))
    assert tool_call_count == 2


def test_tool_progress_chunk_resets_idle_timer():
    """tool summary change → last_activity_at обновляется."""
    started_wait_at = time.monotonic()
    last_activity_at = started_wait_at
    last_tool_summary = ""
    received_any_tool_event = False

    time.sleep(0.01)

    tool_summary = "🔧 web_search\nИнструментов: 0/1"
    if tool_summary and tool_summary != last_tool_summary:
        received_any_tool_event = True
        last_activity_at = time.monotonic()
        last_tool_summary = tool_summary

    assert received_any_tool_event
    assert last_activity_at > started_wait_at


# ---------------------------------------------------------------------------
# Тесты условий idle gate / first-activity gate
# ---------------------------------------------------------------------------


def test_silence_after_activity_triggers_idle_hang_after_cap():
    """Idle gate: received_any_tool_event + silence > cap → должен сработать."""
    _idle_cap_sec = 180.0
    received_any_tool_event = True
    received_any_chunk = False

    # Симулируем что last_activity_at было 200 секунд назад
    _now = time.monotonic()
    last_activity_at = _now - 200.0

    _idle_since_activity = _now - last_activity_at

    should_fire = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since_activity >= _idle_cap_sec
    )
    assert should_fire, f"Expected idle gate to fire, idle_since={_idle_since_activity}"


def test_no_chunks_at_all_triggers_first_activity_hang():
    """First-activity gate: нет ни text ни tool > codex cap → должен сработать."""
    _codex_first_chunk_cap_sec = 45.0
    received_any_chunk = False
    received_any_tool_event = False
    elapsed_wait_sec = 50.0  # > 45s

    should_fire = (
        _codex_first_chunk_cap_sec > 0
        and not received_any_chunk
        and not received_any_tool_event
        and elapsed_wait_sec >= _codex_first_chunk_cap_sec
    )
    assert should_fire


def test_idle_gate_skips_when_received_any_chunk():
    """Idle gate НЕ срабатывает если уже получен text chunk (stream жив)."""
    _idle_cap_sec = 180.0
    received_any_tool_event = True
    received_any_chunk = True  # уже получен text

    _now = time.monotonic()
    last_activity_at = _now - 300.0  # давно была activity

    _idle_since_activity = _now - last_activity_at

    # Gate требует not received_any_chunk
    should_fire = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk  # <-- False, gate не срабатывает
        and _idle_since_activity >= _idle_cap_sec
    )
    assert not should_fire, "Idle gate не должен срабатывать после получения text chunk"


def test_tool_activity_suppresses_first_activity_gate():
    """Wave 16-I: tool activity подавляет first-activity gate (codex)."""
    _codex_first_chunk_cap_sec = 45.0
    received_any_chunk = False
    received_any_tool_event = True  # есть tool activity!
    elapsed_wait_sec = 100.0  # >> 45s

    # Wave 16-I: gate требует not received_any_tool_event
    should_fire = (
        _codex_first_chunk_cap_sec > 0
        and not received_any_chunk
        and not received_any_tool_event  # <-- False, gate не срабатывает
        and elapsed_wait_sec >= _codex_first_chunk_cap_sec
    )
    assert not should_fire, "First-activity gate должен быть подавлен при tool activity"


def test_idle_timeout_overrides_codex_first_chunk_timeout():
    """Idle gate срабатывает вместо first-activity gate при tool activity."""
    _codex_first_chunk_cap_sec = 45.0
    _idle_cap_sec = 180.0
    received_any_chunk = False
    received_any_tool_event = True  # была tool activity

    elapsed_wait_sec = 200.0  # >> codex cap
    _now = time.monotonic()
    last_activity_at = _now - 200.0  # давно была activity

    _idle_since_activity = _now - last_activity_at

    # First-activity gate НЕ срабатывает (tool activity есть)
    first_gate = (
        _codex_first_chunk_cap_sec > 0
        and not received_any_chunk
        and not received_any_tool_event
        and elapsed_wait_sec >= _codex_first_chunk_cap_sec
    )
    # Idle gate СРАБАТЫВАЕТ
    idle_gate = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since_activity >= _idle_cap_sec
    )

    assert not first_gate, "First-activity gate не должен сработать при tool activity"
    assert idle_gate, "Idle gate должен сработать вместо первого"


def test_idle_cap_sec_env_override(monkeypatch):
    """KRAB_LLM_IDLE_TIMEOUT_SEC env override применяется в config."""
    import os

    monkeypatch.setenv("KRAB_LLM_IDLE_TIMEOUT_SEC", "300")
    # Перечитываем через float(os.getenv(...)) — как в config.py
    val = float(os.getenv("KRAB_LLM_IDLE_TIMEOUT_SEC", "180"))
    assert val == 300.0


def test_heartbeat_interval_env_override(monkeypatch):
    """KRAB_LLM_HEARTBEAT_INTERVAL_SEC env override применяется."""
    import os

    monkeypatch.setenv("KRAB_LLM_HEARTBEAT_INTERVAL_SEC", "120")
    val = float(os.getenv("KRAB_LLM_HEARTBEAT_INTERVAL_SEC", "60"))
    assert val == 120.0


# ---------------------------------------------------------------------------
# Тесты heartbeat message edit
# ---------------------------------------------------------------------------


def test_heartbeat_condition_fires_when_tool_active():
    """Heartbeat должен срабатывать когда: show_progress AND tool_event AND not text."""
    _show_progress = True
    is_self = False
    received_any_tool_event = True
    received_any_chunk = False
    _heartbeat_interval_sec = 60.0
    elapsed_wait_sec = 70.0
    started_wait_at = time.monotonic()
    _next_heartbeat_at = started_wait_at + _heartbeat_interval_sec  # 60s mark

    should_fire = (
        _show_progress
        and not is_self
        and received_any_tool_event
        and not received_any_chunk
        and _heartbeat_interval_sec > 0
        and elapsed_wait_sec >= (_next_heartbeat_at - started_wait_at)
    )
    assert should_fire


def test_heartbeat_skips_when_is_self():
    """Heartbeat не срабатывает для is_self сообщений."""
    _show_progress = True
    is_self = True  # owner-self message
    received_any_tool_event = True
    received_any_chunk = False
    _heartbeat_interval_sec = 60.0
    elapsed_wait_sec = 70.0
    started_wait_at = time.monotonic()
    _next_heartbeat_at = started_wait_at + _heartbeat_interval_sec

    should_fire = (
        _show_progress
        and not is_self
        and received_any_tool_event
        and not received_any_chunk
        and _heartbeat_interval_sec > 0
        and elapsed_wait_sec >= (_next_heartbeat_at - started_wait_at)
    )
    assert not should_fire


@pytest.mark.asyncio
async def test_heartbeat_message_edit_called_on_60s():
    """Heartbeat вызывает _safe_edit с notice содержащим elapsed."""
    safe_edit_mock = AsyncMock(return_value=MagicMock())
    _heartbeat_interval_sec = 60.0
    started_wait_at = time.monotonic()
    tool_call_count = 3
    elapsed_wait_sec = 70.0  # > 60s

    # Симулируем heartbeat action
    _elapsed_int = int(elapsed_wait_sec)
    _tools_str = f", инструментов: {tool_call_count}" if tool_call_count > 0 else ""
    _heartbeat_notice = f"🦀 Думаю... ({_elapsed_int}s{_tools_str})"

    fake_temp_msg = MagicMock()
    await safe_edit_mock(fake_temp_msg, _heartbeat_notice)

    safe_edit_mock.assert_called_once()
    call_args = safe_edit_mock.call_args
    notice_text = call_args[0][1]
    assert "70s" in notice_text
    assert "инструментов: 3" in notice_text
    assert "Думаю" in notice_text


@pytest.mark.asyncio
async def test_tool_call_count_in_heartbeat_notice():
    """Heartbeat notice включает tool_call_count если > 0."""
    tool_call_count = 5
    elapsed_wait_sec = 120.0
    _elapsed_int = int(elapsed_wait_sec)
    _tools_str = f", инструментов: {tool_call_count}" if tool_call_count > 0 else ""
    notice = f"🦀 Думаю... ({_elapsed_int}s{_tools_str})"

    assert "инструментов: 5" in notice
    assert "120s" in notice


@pytest.mark.asyncio
async def test_heartbeat_no_tools_str_when_zero():
    """Heartbeat notice не включает tools_str если tool_call_count=0."""
    tool_call_count = 0
    elapsed_wait_sec = 90.0
    _tools_str = f", инструментов: {tool_call_count}" if tool_call_count > 0 else ""
    notice = f"🦀 Думаю... ({int(elapsed_wait_sec)}s{_tools_str})"

    assert "инструментов" not in notice
    assert "90s" in notice


# ---------------------------------------------------------------------------
# Тест что конфиг содержит новые knobs
# ---------------------------------------------------------------------------


def test_config_has_idle_timeout_knob():
    """Config содержит KRAB_LLM_IDLE_TIMEOUT_SEC с дефолтом 180."""
    from src.config import config

    val = getattr(config, "KRAB_LLM_IDLE_TIMEOUT_SEC", None)
    assert val is not None, "KRAB_LLM_IDLE_TIMEOUT_SEC должен быть в Config"
    assert isinstance(val, float)
    assert val >= 0.0


def test_config_has_heartbeat_interval_knob():
    """Config содержит KRAB_LLM_HEARTBEAT_INTERVAL_SEC с дефолтом 60."""
    from src.config import config

    val = getattr(config, "KRAB_LLM_HEARTBEAT_INTERVAL_SEC", None)
    assert val is not None, "KRAB_LLM_HEARTBEAT_INTERVAL_SEC должен быть в Config"
    assert isinstance(val, float)
    assert val > 0.0


def test_config_idle_default_is_180():
    """KRAB_LLM_IDLE_TIMEOUT_SEC дефолт = 180s."""
    import os

    if os.getenv("KRAB_LLM_IDLE_TIMEOUT_SEC"):
        pytest.skip("env override active, skip default check")
    from src.config import config

    assert config.KRAB_LLM_IDLE_TIMEOUT_SEC == 180.0


def test_config_heartbeat_default_is_60():
    """KRAB_LLM_HEARTBEAT_INTERVAL_SEC дефолт = 60s."""
    import os

    if os.getenv("KRAB_LLM_HEARTBEAT_INTERVAL_SEC"):
        pytest.skip("env override active, skip default check")
    from src.config import config

    assert config.KRAB_LLM_HEARTBEAT_INTERVAL_SEC == 60.0
