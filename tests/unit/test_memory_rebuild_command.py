# -*- coding: utf-8 -*-
"""Unit-тесты для `!memory rebuild` subcommand."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Env-vars до импорта src.* (иначе config.py падает на TELEGRAM_API_ID).
for _k, _v in {
    "TELEGRAM_API_ID": "0",
    "TELEGRAM_API_HASH": "test",
    "OWNER_ID": "0",
}.items():
    if not os.environ.get(_k):
        os.environ[_k] = _v

from src.handlers.command_handlers import _handle_memory_rebuild, handle_memory  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot(owner_id: int = 42) -> MagicMock:
    bot = MagicMock()
    bot.me = MagicMock()
    bot.me.id = owner_id
    return bot


def _make_message(text: str, sender_id: int = 42) -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = sender_id
    return msg


def _make_proc(returncode: int = 0) -> MagicMock:
    """Создаёт mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.terminate = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Тест 1: non-owner rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_non_owner_rejected() -> None:
    """!memory rebuild отклоняется для не-владельца."""
    bot = _make_bot(owner_id=42)
    message = _make_message("!memory rebuild", sender_id=99)  # чужой user

    await handle_memory(bot, message)

    message.reply.assert_called_once()
    reply_text = message.reply.call_args[0][0]
    assert "владельцу" in reply_text.lower() or "owner" in reply_text.lower()


# ---------------------------------------------------------------------------
# Тест 2: script not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_script_missing() -> None:
    """Если repair_sqlite_vec.py не найден — выдаёт ошибку без краша."""
    message = _make_message("!memory rebuild", sender_id=42)

    with patch("pathlib.Path.exists", return_value=False):
        await _handle_memory_rebuild(message)

    message.reply.assert_called_once()
    reply_text = message.reply.call_args[0][0]
    assert "not found" in reply_text.lower() or "не найден" in reply_text.lower()


# ---------------------------------------------------------------------------
# Тест 3: success case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_success() -> None:
    """При успешном repair в reply появляется маркер success."""
    message = _make_message("!memory rebuild", sender_id=42)

    # Симулируем успешный процесс
    stdout_data = b"[DONE] Repair done.\nsome output"
    proc = _make_proc(returncode=0)

    async def _fake_wait_for(coro: object, timeout: float) -> tuple[bytes, bytes]:
        return (stdout_data, b"")

    with patch(
        "src.handlers.command_handlers.asyncio.create_subprocess_exec",
        return_value=proc,
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ), patch(
        "src.handlers.command_handlers.asyncio.wait_for",
        side_effect=_fake_wait_for,
    ):
        await _handle_memory_rebuild(message)

    # Должно быть минимум 2 reply: "Запускаю..." + результат
    assert message.reply.call_count >= 2
    # Финальный reply содержит успешный маркер
    final_reply = message.reply.call_args[0][0]
    assert "done" in final_reply.lower() or "repair" in final_reply.lower()


# ---------------------------------------------------------------------------
# Тест 4: timeout case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_timeout() -> None:
    """При timeout subprocess терминируется и отправляется предупреждение."""
    message = _make_message("!memory rebuild", sender_id=42)

    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()

    async def _raise_timeout(*_args: object, **_kwargs: object) -> None:
        raise asyncio.TimeoutError

    with patch(
        "src.handlers.command_handlers.asyncio.create_subprocess_exec",
        return_value=proc,
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ), patch(
        "src.handlers.command_handlers.asyncio.wait_for",
        side_effect=_raise_timeout,
    ):
        await _handle_memory_rebuild(message)

    # terminate вызван
    proc.terminate.assert_called_once()
    # отправлено предупреждение о timeout
    final_text = message.reply.call_args[0][0]
    assert "timeout" in final_text.lower() or "Repair timeout" in final_text


# ---------------------------------------------------------------------------
# Тест 5: non-zero exit code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_rebuild_nonzero_exit() -> None:
    """При returncode != 0 сообщается об ошибке."""
    message = _make_message("!memory rebuild", sender_id=42)

    stdout_data = b"[ERROR] something went wrong"
    proc = _make_proc(returncode=1)

    async def _fake_wait_for(coro: object, timeout: float) -> tuple[bytes, bytes]:
        return (stdout_data, b"")

    with patch(
        "src.handlers.command_handlers.asyncio.create_subprocess_exec",
        return_value=proc,
    ), patch(
        "pathlib.Path.exists",
        return_value=True,
    ), patch(
        "src.handlers.command_handlers.asyncio.wait_for",
        side_effect=_fake_wait_for,
    ):
        await _handle_memory_rebuild(message)

    # Финальный reply сообщает об ошибке
    final_text = message.reply.call_args[0][0]
    assert "\u274c" in final_text or "код" in final_text.lower()
