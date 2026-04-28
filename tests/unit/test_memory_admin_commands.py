# -*- coding: utf-8 -*-
"""
Phase 2 Wave 18 regression-сьют для memory_admin_commands.

Проверяет, что extracted модуль доступен напрямую и что dual-namespace
lookup работает (патчи через `command_handlers.<symbol>` подхватываются).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.handlers import command_handlers as ch
from src.handlers.commands import memory_admin_commands as m


def test_module_exposes_public_surface() -> None:
    """Все ключевые символы извлечены и присутствуют в новом модуле."""
    for name in (
        "handle_memory",
        "_handle_memory_stats",
        "_handle_memory_clear",
        "_handle_memory_rebuild",
        "_collect_memory_archive_stats",
        "_collect_memory_indexer_stats",
        "_collect_memory_validator_stats",
        "_fmt_int_ru",
        "format_memory_stats",
        "_ARCHIVE_DB_PATH_FOR_CLEAR",
        "_REPAIR_SCRIPT_RELPATH",
    ):
        assert hasattr(m, name), name


def test_command_handlers_reexports_memory_surface() -> None:
    """command_handlers re-export всё, на что опирались тесты Wave 11/17."""
    assert ch.handle_memory is m.handle_memory
    assert ch._handle_memory_stats is m._handle_memory_stats
    assert ch._handle_memory_clear is m._handle_memory_clear
    assert ch._handle_memory_rebuild is m._handle_memory_rebuild
    assert ch.format_memory_stats is m.format_memory_stats
    assert ch._ARCHIVE_DB_PATH_FOR_CLEAR == m._ARCHIVE_DB_PATH_FOR_CLEAR


def test_fmt_int_ru_inserts_thin_space_separator() -> None:
    assert m._fmt_int_ru(1234) == "1 234"
    assert m._fmt_int_ru(0) == "0"
    assert m._fmt_int_ru(1_234_567) == "1 234 567"


@pytest.mark.asyncio
async def test_handle_memory_dispatches_via_command_handlers_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dual-namespace: патч ch._handle_memory_stats подхватывается оркестратором."""
    captured: list[str] = []

    async def _spy(message):  # type: ignore[no-untyped-def]
        captured.append("stats")
        await message.reply("ok")

    monkeypatch.setattr(ch, "_handle_memory_stats", _spy)

    msg = MagicMock()
    msg.text = "!memory stats"
    msg.reply = AsyncMock()
    bot = MagicMock()

    await ch.handle_memory(bot, msg)
    assert captured == ["stats"]
