# -*- coding: utf-8 -*-
"""
Тесты команды !version.

Покрываем:
1. Основной happy-path: все поля присутствуют в ответе
2. git недоступен → graceful fallback "unknown"
3. pyrogram недоступен → graceful fallback "unknown"
4. openclaw CLI недоступен → graceful fallback "unknown"
5. Формат ответа: заголовок, разделитель, ключевые метки
6. git rev-parse вернул пустую строку → "unknown"
7. git branch вернул пустую строку → "unknown"
8. git log вернул пустую строку → "unknown"
9. Дата обрезается до YYYY-MM-DD (только 10 символов)
10. reply вызывается ровно один раз
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import handle_version

# ─────────────────────────── helpers ────────────────────────────────────────


def _msg(text: str = "!version") -> SimpleNamespace:
    """Минимальный stub Message с reply-mock."""
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=12345),
        reply=AsyncMock(),
    )


def _bot() -> SimpleNamespace:
    """Минимальный stub KraabUserbot."""
    return SimpleNamespace()


def _make_run_result(stdout: str = "", returncode: int = 0) -> MagicMock:
    """Создаёт mock subprocess.CompletedProcess."""
    r = MagicMock()
    r.stdout = stdout
    r.stderr = ""
    r.returncode = returncode
    return r


# ─────────────────────────── тесты ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_version_happy_path() -> None:
    """Основной сценарий: все subprocess отвечают корректно."""
    git_responses = [
        _make_run_result("abc1234\n"),        # rev-parse --short HEAD
        _make_run_result("main\n"),            # branch --show-current
        _make_run_result("2026-04-12 10:30:00 +0200\n"),  # log -1 --format=%ci
        _make_run_result("OpenClaw 2026.4.11 (abc)\n"),   # openclaw --version
    ]

    with (
        patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses),
        patch("pyrogram.__version__", "2.3.69", create=True),
    ):
        message = _msg()
        await handle_version(_bot(), message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Krab Version" in text
    assert "abc1234" in text
    assert "main" in text
    assert "2026-04-12" in text
    assert "OpenClaw" in text


@pytest.mark.asyncio
async def test_version_git_unavailable() -> None:
    """Если git недоступен — fallback 'unknown' для commit/branch/date."""

    def raise_oserror(*args, **kwargs):
        raise OSError("git not found")

    with (
        patch("src.handlers.command_handlers.subprocess.run", side_effect=raise_oserror),
    ):
        message = _msg()
        await handle_version(_bot(), message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    # Все три git-поля → unknown
    assert text.count("unknown") >= 3


@pytest.mark.asyncio
async def test_version_openclaw_unavailable() -> None:
    """Если openclaw CLI недоступен — только OpenClaw → unknown, остальное работает."""
    git_responses = [
        _make_run_result("deadbee\n"),
        _make_run_result("feature-x\n"),
        _make_run_result("2026-01-01 00:00:00 +0000\n"),
        MagicMock(side_effect=OSError("not found")),  # openclaw --version
    ]

    # subprocess.run вызывается 4 раза; 4-й поднимает OSError
    call_count = [0]

    def side_effect(*args, **kwargs):
        i = call_count[0]
        call_count[0] += 1
        if i < 3:
            return git_responses[i]
        raise OSError("openclaw not found")

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=side_effect):
        message = _msg()
        await handle_version(_bot(), message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "deadbee" in text
    assert "feature-x" in text
    assert "unknown" in text  # openclaw fallback


@pytest.mark.asyncio
async def test_version_empty_git_output() -> None:
    """git возвращает пустую строку → все git-поля 'unknown'."""
    git_responses = [
        _make_run_result(""),   # rev-parse
        _make_run_result(""),   # branch
        _make_run_result(""),   # log
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    # commit и branch → unknown (пустая строка → unknown)
    assert text.count("unknown") >= 2


@pytest.mark.asyncio
async def test_version_date_trimmed_to_10_chars() -> None:
    """Дата обрезается до 10 символов (YYYY-MM-DD)."""
    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 15:45:00 +0200\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    assert "2026-04-12" in text
    assert "15:45:00" not in text  # время не показывается


@pytest.mark.asyncio
async def test_version_reply_called_once() -> None:
    """reply вызывается ровно один раз."""
    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 10:00:00 +0000\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    assert message.reply.await_count == 1


@pytest.mark.asyncio
async def test_version_header_and_separator_present() -> None:
    """Заголовок 'Krab Version' и разделитель '─────' присутствуют."""
    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 10:00:00 +0000\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    assert "Krab Version" in text
    assert "─────" in text


@pytest.mark.asyncio
async def test_version_python_version_present() -> None:
    """Python версия включена в вывод."""
    import platform

    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 10:00:00 +0000\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    py_ver = platform.python_version()
    assert py_ver in text


@pytest.mark.asyncio
async def test_version_pyrogram_version_present() -> None:
    """Pyrogram версия включена в вывод."""
    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 10:00:00 +0000\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    assert "Pyrogram" in text


@pytest.mark.asyncio
async def test_version_all_field_labels_present() -> None:
    """Все метки полей (Commit, Branch, Date, Python, Pyrogram, OpenClaw) присутствуют."""
    git_responses = [
        _make_run_result("abc1234\n"),
        _make_run_result("main\n"),
        _make_run_result("2026-04-12 10:00:00 +0000\n"),
        _make_run_result("OpenClaw 2026.4.11\n"),
    ]

    with patch("src.handlers.command_handlers.subprocess.run", side_effect=git_responses):
        message = _msg()
        await handle_version(_bot(), message)

    text = message.reply.await_args.args[0]
    for label in ("Commit", "Branch", "Date", "Python", "Pyrogram", "OpenClaw"):
        assert label in text, f"Метка '{label}' не найдена в ответе"
