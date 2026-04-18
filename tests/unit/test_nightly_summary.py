# -*- coding: utf-8 -*-
"""
Тесты для NightlySummaryService и generate_summary().

Паттерн: mock внешних зависимостей (sqlite3, cost_analytics, inbox_service,
swarm_artifact_store, krab_scheduler).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.nightly_summary import (
    NightlySummaryService,
    generate_summary,
    nightly_summary_service,
    send_nightly_summary,
)

# ---------------------------------------------------------------------------
# 1. generate_summary — содержит дату
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_summary_contains_date():
    """Digest содержит сегодняшнюю дату в заголовке."""
    from datetime import datetime

    result = await generate_summary()
    today = datetime.now().strftime("%Y-%m-%d")
    assert "Krab Daily Digest" in result
    assert today in result


# ---------------------------------------------------------------------------
# 2. generate_summary — graceful при отсутствии зависимостей
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_summary_graceful_on_missing_deps():
    """Digest не падает если archive.db нет и остальные зависимости не доступны."""
    # Мокируем все _append_* функции как no-op (они сами ловят исключения внутри)
    with (
        patch("src.core.nightly_summary._append_archive_stats"),
        patch("src.core.nightly_summary._append_swarm_stats"),
        patch("src.core.nightly_summary._append_cost_stats"),
        patch("src.core.nightly_summary._append_inbox_stats"),
        patch("src.core.nightly_summary._append_reminder_stats"),
    ):
        # generate_summary не должна бросать исключение
        result = await generate_summary()
    # Всё равно возвращает строку с заголовком
    assert isinstance(result, str)
    assert "Krab Daily Digest" in result


# ---------------------------------------------------------------------------
# 3. generate_summary — с mock sqlite (archive stats)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_summary_with_archive():
    """Archive stats корректно отображаются при наличии db."""
    # Мокируем Path.exists() и sqlite3 внутри _append_archive_stats
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = [
        MagicMock(fetchone=lambda: (1234,)),   # total
        MagicMock(fetchone=lambda: (42,)),     # today
    ]
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_stat = MagicMock()
    mock_stat.st_size = 5 * 1024 * 1024  # 5 MB

    with (
        patch("pathlib.Path.exists", return_value=True),
        patch("pathlib.Path.stat", return_value=mock_stat),
        patch("sqlite3.connect", return_value=mock_conn),
        # Заглушаем остальные секции чтобы не падали
        patch("src.core.nightly_summary._append_swarm_stats"),
        patch("src.core.nightly_summary._append_cost_stats"),
        patch("src.core.nightly_summary._append_inbox_stats"),
        patch("src.core.nightly_summary._append_reminder_stats"),
    ):
        result = await generate_summary()

    assert "Memory Archive" in result
    assert "1 234" in result or "1,234" in result
    assert "5.0 MB" in result
    assert "+42" in result


# ---------------------------------------------------------------------------
# 4. send_nightly_summary — использует первый OWNER_USER_ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_summary_uses_first_owner_id():
    """send_nightly_summary принимает owner_chat_id явно и отправляет в него."""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(return_value=None)

    with patch("src.core.nightly_summary.generate_summary", return_value="test digest"):
        result = await send_nightly_summary(mock_bot, owner_chat_id="123456789")

    assert result is True
    mock_bot.send_message.assert_called_once_with(
        123456789, "test digest", parse_mode="markdown"
    )


# ---------------------------------------------------------------------------
# 5. send_nightly_summary — возвращает False если owner_id не настроен
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_summary_returns_false_if_no_owner():
    """Если OWNER_USER_IDS пуст — возвращает False, не падает."""
    mock_bot = AsyncMock()

    with (
        patch("src.core.nightly_summary.generate_summary", return_value="test"),
        patch("src.config.config.OWNER_USER_IDS", []),
    ):
        result = await send_nightly_summary(mock_bot)

    assert result is False
    mock_bot.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# 6. send_nightly_summary — возвращает False при ошибке отправки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_summary_returns_false_on_send_error():
    """Ошибка при send_message возвращает False, не бросает исключение."""
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=Exception("network error"))

    with patch(
        "src.core.nightly_summary.generate_summary", return_value="test"
    ):
        result = await send_nightly_summary(mock_bot, owner_chat_id="99")

    assert result is False


# ---------------------------------------------------------------------------
# 7. NightlySummaryService.bind_bot / start (smoke)
# ---------------------------------------------------------------------------


def test_service_bind_bot():
    """bind_bot сохраняет объект бота."""
    svc = NightlySummaryService()
    fake_bot = object()
    svc.bind_bot(fake_bot)
    assert svc._bot is fake_bot


def test_service_start_returns_task():
    """start() создаёт asyncio.Task."""
    svc = NightlySummaryService()
    fake_bot = MagicMock()
    svc.bind_bot(fake_bot)

    async def _run():
        task = svc.start()
        assert isinstance(task, asyncio.Task)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# 8. Синглтон экспортируется
# ---------------------------------------------------------------------------


def test_singleton_exported():
    """nightly_summary_service — это NightlySummaryService."""
    assert isinstance(nightly_summary_service, NightlySummaryService)
