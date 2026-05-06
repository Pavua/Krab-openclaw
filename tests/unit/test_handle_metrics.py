# -*- coding: utf-8 -*-
"""
tests/unit/test_handle_metrics.py — Wave 39-A

5 тестов для handle_metrics:
  1. Все источники доступны → полный вывод
  2. Bypass perf endpoint недоступен → graceful (предупреждение в тексте)
  3. Нет zombie-событий → строка zombie отсутствует
  4. Нет файла coexistence_monitor.log → строка Memory отсутствует
  5. Вывод отформатирован как Markdown (заголовок и жирные секции)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PERF_RESPONSE = json.dumps(
    {
        "total_calls": 42,
        "total_failures": 1,
        "by_kind": {
            "gemini-cli": {"count": 30, "p95": 1.23},
            "codex": {"count": 10, "p95": 2.10},
            "anthropic-vertex": {"count": 2, "p95": 0.55},
        },
    }
).encode()

_QUOTA_RESPONSE = json.dumps(
    {
        "providers": {
            "gemini-cli": {"today_calls": 30},
            "codex": {"today_calls": 10},
            "vertex": {"today_calls": 5},
            "anthropic": {"today_calls": 2},
        }
    }
).encode()

_COEXISTENCE_LOG_ENTRY = json.dumps(
    {
        "krab_rss_gb": 1.2,
        "ear_rss_gb": 0.3,
        "swap_used_gb": 0.5,
        "system_ram_available_gb": 8.1,
    }
).encode() + b"\n"


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.text = "!metrics"
    msg.reply = AsyncMock()
    return msg


def _make_bot() -> MagicMock:
    return MagicMock()


def _make_urlopen_side_effect(
    *,
    perf_data: bytes | None = _PERF_RESPONSE,
    quota_data: bytes | None = _QUOTA_RESPONSE,
) -> Any:
    """Фабрика context-manager mock для urllib.request.urlopen."""

    def _urlopen(url, timeout=None):  # noqa: ARG001
        cm = MagicMock()
        if "bypass/perf" in str(url):
            if perf_data is None:
                raise OSError("connection refused (mocked)")
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read = MagicMock(return_value=perf_data)
        else:
            # quota endpoint
            if quota_data is None:
                raise OSError("connection refused (mocked)")
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read = MagicMock(return_value=quota_data if quota_data else b"{}")
        return cm

    return _urlopen


# ---------------------------------------------------------------------------
# Тест 1: все источники доступны → полный вывод
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_metrics_all_sources_ok(tmp_path: Path) -> None:
    """Когда все источники отвечают, вывод содержит все разделы."""
    from src.handlers.commands.observability_commands import handle_metrics

    # coexistence_monitor.log
    log_dir = tmp_path / ".openclaw" / "krab_runtime_state"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "coexistence_monitor.log"
    log_file.write_bytes(_COEXISTENCE_LOG_ENTRY)

    # krab_main.log с одной zombie строкой
    krab_log = log_dir / "krab_main.log"
    krab_log.write_text("telegram_session_zombie_escalation: occurred\n")

    msg = _make_message()
    bot = _make_bot()

    with (
        patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect()),
        patch("psutil.process_iter", return_value=[]),
        patch(
            "subprocess.run",
            side_effect=[
                # launchctl list — daemons
                MagicMock(returncode=0, stdout="- 0 ai.krab.core\n123 0 ai.krab.mcp\n"),
                # grep zombie
                MagicMock(returncode=0, stdout="1\n"),
            ],
        ),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        await handle_metrics(bot, msg)

    msg.reply.assert_awaited_once()
    text: str = msg.reply.call_args[0][0]

    assert "Krab Metrics" in text
    assert "Bypass" in text
    assert "42 calls" in text
    assert "gemini-cli" in text
    assert "Today's calls" in text
    assert "Memory" in text
    assert "krab=1.2GB" in text
    assert "Daemons" in text
    assert "Zombie escalations" in text


# ---------------------------------------------------------------------------
# Тест 2: bypass perf endpoint недоступен → graceful (предупреждение)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_metrics_bypass_down() -> None:
    """Если /api/bypass/perf недоступен, команда не падает, показывает ⚠️."""
    from src.handlers.commands.observability_commands import handle_metrics

    msg = _make_message()
    bot = _make_bot()

    with (
        patch(
            "urllib.request.urlopen",
            side_effect=_make_urlopen_side_effect(perf_data=None),
        ),
        patch("psutil.process_iter", return_value=[]),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=""),
        ),
        patch("pathlib.Path.home", return_value=Path("/nonexistent_path_xyz")),
    ):
        # Quota fallback через _count_today_calls тоже может упасть — не страшно
        await handle_metrics(bot, msg)

    msg.reply.assert_awaited_once()
    text: str = msg.reply.call_args[0][0]
    assert "Bypass" in text
    assert "⚠️" in text


# ---------------------------------------------------------------------------
# Тест 3: нет zombie-событий → строка zombie отсутствует
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_metrics_no_zombies(tmp_path: Path) -> None:
    """Если zombie_count == 0, раздел 🧟 не появляется."""
    from src.handlers.commands.observability_commands import handle_metrics

    log_dir = tmp_path / ".openclaw" / "krab_runtime_state"
    log_dir.mkdir(parents=True)
    krab_log = log_dir / "krab_main.log"
    krab_log.write_text("INFO normal startup\n")

    msg = _make_message()
    bot = _make_bot()

    with (
        patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect()),
        patch("psutil.process_iter", return_value=[]),
        patch(
            "subprocess.run",
            side_effect=[
                MagicMock(returncode=0, stdout="- 0 ai.krab.core\n"),
                # grep возвращает 0 совпадений
                MagicMock(returncode=1, stdout="0\n"),
            ],
        ),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        await handle_metrics(bot, msg)

    text: str = msg.reply.call_args[0][0]
    assert "Zombie" not in text


# ---------------------------------------------------------------------------
# Тест 4: нет файла coexistence_monitor.log → раздел Memory отсутствует
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_metrics_no_memory_log(tmp_path: Path) -> None:
    """Если coexistence_monitor.log не существует, раздел 🖥 Memory не появляется."""
    from src.handlers.commands.observability_commands import handle_metrics

    # НЕ создаём coexistence_monitor.log
    msg = _make_message()
    bot = _make_bot()

    with (
        patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect()),
        patch("psutil.process_iter", return_value=[]),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout=""),
        ),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        await handle_metrics(bot, msg)

    text: str = msg.reply.call_args[0][0]
    assert "Memory" not in text


# ---------------------------------------------------------------------------
# Тест 5: вывод отформатирован как Markdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_metrics_markdown_format(tmp_path: Path) -> None:
    """Вывод начинается с заголовка и содержит markdown-маркеры жирного текста."""
    from pyrogram.enums import ParseMode

    from src.handlers.commands.observability_commands import handle_metrics

    msg = _make_message()
    bot = _make_bot()

    with (
        patch("urllib.request.urlopen", side_effect=_make_urlopen_side_effect()),
        patch("psutil.process_iter", return_value=[]),
        patch(
            "subprocess.run",
            return_value=MagicMock(returncode=0, stdout="- 0 ai.krab.core\n"),
        ),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        await handle_metrics(bot, msg)

    text: str = msg.reply.call_args[0][0]
    kwargs: dict = msg.reply.call_args[1]

    # Заголовок
    assert text.startswith("📊 *Krab Metrics*")
    # Markdown-маркеры присутствуют
    assert "*" in text
    # ParseMode передан как enum
    assert kwargs.get("parse_mode") == ParseMode.MARKDOWN
