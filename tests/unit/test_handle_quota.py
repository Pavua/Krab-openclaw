# -*- coding: utf-8 -*-
"""
Тесты Wave 25-D: handle_quota.

Проверяем:
1) happy path — все probe ok, счётчики из лога (4 tests);
2) graceful timeout при probe gemini-cli;
3) корректный подсчёт bypass-вызовов из лог-файла;
4) --no-probe флаг пропускает probe-вызовы.
"""

from __future__ import annotations

import asyncio
import pathlib
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.commands.observability_commands import (
    _count_today_calls,
    handle_quota,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str, chat_id: int = -100123) -> SimpleNamespace:
    """Минимальный mock Pyrogram Message."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
    )


def _make_bot() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# Test 1: happy path — все probe ✅, счётчики из лога
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_quota_happy_path(tmp_path: pathlib.Path) -> None:
    """Когда все probe возвращают ok — сообщение содержит ✅ ok и счётчики."""
    # Синтетический лог с bypass-вызовами за сегодня
    today = "2026-05-05"
    log = tmp_path / "krab_main.log"
    log.write_text(
        textwrap.dedent(f"""
        {today} INFO cli_subprocess_bypass_engaged binary=gemini model=gemini-2.5-flash
        {today} INFO cli_subprocess_bypass_engaged binary=gemini model=gemini-2.5-flash
        {today} INFO cli_subprocess_bypass_engaged binary=codex model=gpt-4o
        {today} INFO google_vertex_bypass_engaged project=caramel
        {today} INFO anthropic_vertex_bypass_engaged project=caramel
        """),
        encoding="utf-8",
    )

    message = _make_message("!quota")

    with (
        patch(
            "src.handlers.commands.observability_commands._probe_gemini_cli",
            AsyncMock(return_value="✅ ok"),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_anthropic_vertex",
            AsyncMock(return_value="✅ ok"),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_vertex_gemini",
            AsyncMock(return_value="✅ ok"),
        ),
        patch(
            "src.handlers.commands.observability_commands._LOG_FILE",
            log,
        ),
        patch(
            "src.handlers.commands.observability_commands.datetime",
        ) as mock_dt,
    ):
        mock_dt.datetime.now.return_value.strftime.return_value = today
        await handle_quota(_make_bot(), message)

    message.reply.assert_awaited_once()
    text: str = message.reply.call_args[0][0]

    assert "✅ ok" in text
    assert "2 calls (bypass)" in text   # gemini: 2
    assert "1 calls" in text            # codex: 1
    assert "Quota Status" in text


# ---------------------------------------------------------------------------
# Test 2: gemini probe timeout — отображается "⏱ timeout"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_quota_timeout_graceful() -> None:
    """При таймауте probe gemini-cli сообщение содержит ⏱ timeout, не падает."""
    message = _make_message("!quota")

    with (
        patch(
            "src.handlers.commands.observability_commands._probe_gemini_cli",
            AsyncMock(return_value="⏱ timeout"),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_anthropic_vertex",
            AsyncMock(return_value="✅ ok"),
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_vertex_gemini",
            AsyncMock(return_value="✅ ok"),
        ),
        patch(
            "src.handlers.commands.observability_commands._LOG_FILE",
            pathlib.Path("/nonexistent/log.txt"),
        ),
    ):
        await handle_quota(_make_bot(), message)

    message.reply.assert_awaited_once()
    text: str = message.reply.call_args[0][0]
    assert "⏱ timeout" in text
    # Остальные probe всё равно показывают ok
    assert "✅ ok" in text


# ---------------------------------------------------------------------------
# Test 3: _count_today_calls читает лог-файл правильно
# ---------------------------------------------------------------------------


def test_count_today_calls_correct(tmp_path: pathlib.Path) -> None:
    """_count_today_calls возвращает правильные счётчики для каждого провайдера."""
    today = "2026-05-05"
    log = tmp_path / "test.log"
    log.write_text(
        textwrap.dedent(f"""
        2026-05-04 INFO cli_subprocess_bypass_engaged binary=gemini  # вчера — не считаем
        {today} INFO cli_subprocess_bypass_engaged binary=gemini model=gemini-2.5-flash
        {today} INFO cli_subprocess_bypass_engaged binary=gemini model=gemini-2.5-pro
        {today} INFO cli_subprocess_bypass_engaged binary=codex model=gpt-4o
        {today} INFO cli_subprocess_bypass_engaged binary=codex model=gpt-4o
        {today} INFO cli_subprocess_bypass_engaged binary=codex model=gpt-4o
        {today} INFO google_vertex_bypass_engaged project=caramel-anvil
        {today} INFO anthropic_vertex_bypass_engaged project=caramel-anvil
        {today} INFO anthropic_vertex_bypass_engaged project=caramel-anvil
        """),
        encoding="utf-8",
    )

    counts = _count_today_calls(log, today)

    assert counts["gemini"] == 2
    assert counts["codex"] == 3
    assert counts["vertex"] == 1
    assert counts["anthropic"] == 2


def test_count_today_calls_missing_file(tmp_path: pathlib.Path) -> None:
    """Если лог-файл отсутствует — возвращаем нули (без исключений)."""
    counts = _count_today_calls(tmp_path / "no_such.log", "2026-05-05")
    assert counts == {"gemini": 0, "codex": 0, "vertex": 0, "anthropic": 0}


# ---------------------------------------------------------------------------
# Test 4: --no-probe пропускает probe-вызовы
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_quota_no_probe_skips_probes() -> None:
    """С флагом --no-probe probe-функции не вызываются."""
    message = _make_message("!quota --no-probe")

    gemini_probe = AsyncMock(return_value="✅ ok")
    vertex_probe = AsyncMock(return_value="✅ ok")
    anthropic_probe = AsyncMock(return_value="✅ ok")

    with (
        patch(
            "src.handlers.commands.observability_commands._probe_gemini_cli",
            gemini_probe,
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_anthropic_vertex",
            anthropic_probe,
        ),
        patch(
            "src.handlers.commands.observability_commands._probe_vertex_gemini",
            vertex_probe,
        ),
        patch(
            "src.handlers.commands.observability_commands._LOG_FILE",
            pathlib.Path("/nonexistent/log.txt"),
        ),
    ):
        await handle_quota(_make_bot(), message)

    # Probe-функции не должны быть вызваны при --no-probe
    gemini_probe.assert_not_awaited()
    vertex_probe.assert_not_awaited()
    anthropic_probe.assert_not_awaited()

    message.reply.assert_awaited_once()
    text: str = message.reply.call_args[0][0]
    # В тексте должно быть указание что probe пропущен
    assert "--no-probe" in text
