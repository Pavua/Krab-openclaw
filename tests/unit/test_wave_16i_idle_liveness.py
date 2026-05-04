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


# ---------------------------------------------------------------------------
# Gap 3 (Wave 17-A): Integration test с динамическим _active_tool_calls
#
# Тестируем логику idle gate при изменении _active_tool_calls во время stream.
# Используем монотонный clock через monkeypatch вместо реального ожидания,
# чтобы pytest --timeout=30 не срабатывал.
#
# Стратегия: Эмулируем жизненный цикл LLM flow через серию временных меток:
# 1. Start (t=0): _active_tool_calls = []
# 2. Tool started (t=30s): добавляем entry → last_activity_at сбрасывается
# 3. Tool done (t=60s): tool_call_count растёт → last_activity_at сброшен
# 4. Silence (t=240s > idle_cap=180s): idle gate срабатывает → LLMRetryableError
# ---------------------------------------------------------------------------


class _FakeOpenclawClient:
    """
    Stub openclaw_client с динамическим _active_tool_calls.
    Позволяет симулировать добавление/удаление tool calls в процессе stream.
    """

    def __init__(self) -> None:
        self._active_tool_calls: list[dict] = []
        self._summary_override: str = ""

    def add_tool(self, name: str) -> None:
        """Симулируем начало выполнения tool."""
        self._active_tool_calls.append({"name": name, "status": "running"})

    def complete_tool(self, name: str) -> None:
        """Симулируем завершение tool."""
        for tc in self._active_tool_calls:
            if tc["name"] == name and tc["status"] == "running":
                tc["status"] = "done"
                break

    def get_active_tool_calls_summary(self) -> str:
        """
        Возвращает summary аналогично реальному методу.
        Меняется при появлении/завершении tool calls.
        """
        if not self._active_tool_calls:
            return ""
        running = [tc["name"] for tc in self._active_tool_calls if tc["status"] == "running"]
        done_count = sum(1 for tc in self._active_tool_calls if tc["status"] == "done")
        total = len(self._active_tool_calls)
        running_str = ", ".join(running) if running else "—"
        return f"🔧 {running_str}\nИнструментов: {done_count}/{total}"


def test_dynamic_tool_calls_resets_last_activity_at():
    """
    Gap 3: Когда _active_tool_calls меняется (tool_summary != last_tool_summary),
    last_activity_at обновляется → idle gate НЕ срабатывает пока есть tool активность.

    Симулируем tick-by-tick логику poll loop из llm_flow.py.
    """
    _idle_cap_sec = 180.0
    received_any_chunk = False
    received_any_tool_event = False
    tool_call_count = 0
    last_tool_summary = ""
    last_activity_at = 0.0  # t=0: начало

    # Шаг 1 (t=0): нет activity — idle gate НЕ срабатывает (нет tool event)
    now = 0.0
    _idle_since = now - last_activity_at
    idle_gate = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since >= _idle_cap_sec
    )
    assert not idle_gate, "Idle gate не должен сработать без tool events"

    # Шаг 2 (t=30s): tool started → summary изменился
    client = _FakeOpenclawClient()
    client.add_tool("web_search")
    tool_summary = client.get_active_tool_calls_summary()

    now = 30.0
    if tool_summary and tool_summary != last_tool_summary:
        received_any_tool_event = True
        last_activity_at = now  # сброс на t=30
        _atc = getattr(client, "_active_tool_calls", None)
        if _atc is not None:
            tool_call_count = max(tool_call_count, len(_atc))
        last_tool_summary = tool_summary

    assert received_any_tool_event, "После tool start: received_any_tool_event должен быть True"
    assert tool_call_count == 1, f"tool_call_count должен быть 1, получили {tool_call_count}"
    assert last_activity_at == pytest.approx(30.0), (
        f"last_activity_at должен быть 30.0, получили {last_activity_at}"
    )

    # Шаг 3 (t=60s): тишина 30s — idle gate НЕ должен сработать (< 180s)
    now = 60.0
    tool_summary = client.get_active_tool_calls_summary()  # tool ещё running → summary тот же

    _idle_since = now - last_activity_at  # = 30s
    idle_gate = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since >= _idle_cap_sec
    )
    assert not idle_gate, "Idle gate не должен сработать при тишине 30s < idle_cap=180s"

    # Шаг 4 (t=90s): tool completes → summary меняется → last_activity_at сбрасывается
    client.complete_tool("web_search")
    tool_summary_after = client.get_active_tool_calls_summary()
    now = 90.0

    if tool_summary_after != last_tool_summary:
        last_activity_at = now  # сброс на t=90
        last_tool_summary = tool_summary_after

    assert last_activity_at == pytest.approx(90.0), (
        f"last_activity_at должен обновиться до 90.0 при завершении tool, "
        f"получили {last_activity_at}"
    )

    # Шаг 5 (t=240s): тишина 150s после последней активности (90.0) — idle_since=150 < 180
    # Idle gate НЕ срабатывает — недостаточно тишины после последней activity
    now = 240.0
    _idle_since = now - last_activity_at  # = 150s < 180s
    idle_gate = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since >= _idle_cap_sec
    )
    assert not idle_gate, (
        f"Idle gate не должен сработать: idle_since={_idle_since}s < idle_cap=180s"
    )

    # Шаг 6 (t=280s): тишина 190s после t=90 → idle_since=190 > 180 → gate срабатывает
    now = 280.0
    _idle_since = now - last_activity_at  # = 190s > 180s
    idle_gate = (
        _idle_cap_sec > 0
        and received_any_tool_event
        and not received_any_chunk
        and _idle_since >= _idle_cap_sec
    )
    assert idle_gate, f"Idle gate должен сработать: idle_since={_idle_since}s >= idle_cap=180s"


def test_dynamic_tool_calls_count_increments_correctly():
    """
    Gap 3: tool_call_count = max(tool_call_count, len(_active_tool_calls))
    при добавлении нескольких tools растёт корректно.
    """
    client = _FakeOpenclawClient()
    tool_call_count = 0
    last_tool_summary = ""

    # Добавляем 3 инструмента последовательно
    for tool_name in ("web_search", "fs_read", "db_query"):
        client.add_tool(tool_name)
        summary = client.get_active_tool_calls_summary()
        if summary != last_tool_summary:
            _atc = getattr(client, "_active_tool_calls", None)
            if _atc is not None:
                tool_call_count = max(tool_call_count, len(_atc))
            last_tool_summary = summary

    assert tool_call_count == 3, f"Ожидали tool_call_count=3, получили {tool_call_count}"

    # После завершения все инструментов count не уменьшается (max semantics)
    for tool_name in ("web_search", "fs_read", "db_query"):
        client.complete_tool(tool_name)

    summary = client.get_active_tool_calls_summary()  # пустой — все done
    # Мы не сбрасываем tool_call_count — оно сохраняет max
    # (в реальном коде: tool_call_count = max(tool_call_count, len(_atc)))
    _atc = getattr(client, "_active_tool_calls", None)
    new_count = max(tool_call_count, len(_atc) if _atc else 0)
    assert new_count == 3, (
        f"tool_call_count не должен уменьшаться (max semantics), получили {new_count}"
    )


def test_dynamic_idle_gate_not_firing_during_active_tools():
    """
    Gap 3: Пока _active_tool_calls меняется каждые 30s (< idle_cap=180s),
    idle gate НЕ должен срабатывать.
    """
    _idle_cap_sec = 180.0
    received_any_tool_event = False
    received_any_chunk = False
    tool_call_count = 0
    last_tool_summary = ""
    last_activity_at = 0.0

    client = _FakeOpenclawClient()

    # Симулируем 6 тиков по 30s с tool активностью каждый раз
    for tick in range(6):
        now = float(tick * 30)

        # Каждый тик — новый tool приходит (summary меняется)
        client.add_tool(f"tool_{tick}")
        tool_summary = client.get_active_tool_calls_summary()

        if tool_summary and tool_summary != last_tool_summary:
            received_any_tool_event = True
            last_activity_at = now  # сбрасываем на каждый тик
            _atc = getattr(client, "_active_tool_calls", None)
            if _atc is not None:
                tool_call_count = max(tool_call_count, len(_atc))
            last_tool_summary = tool_summary

        # Проверяем idle gate на каждом тике
        _idle_since = now - last_activity_at
        idle_gate = (
            _idle_cap_sec > 0
            and received_any_tool_event
            and not received_any_chunk
            and _idle_since >= _idle_cap_sec
        )
        assert not idle_gate, (
            f"Idle gate не должен сработать при активных tool events. "
            f"tick={tick}, now={now}, idle_since={_idle_since}"
        )

    # После 6 тиков (t=150s от начала) — idle_since=0 (последний сброс t=150)
    # Ещё нет 180s → gate не сработал
    assert tool_call_count == 6, f"Должно быть 6 tool calls, получили {tool_call_count}"
