# -*- coding: utf-8 -*-
"""
Тесты Telegram callback в WeeklyDigestService.

Session 7: добавлен set_telegram_callback(cb) и вызов callback после inbox upsert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.weekly_digest import WeeklyDigestService


@pytest.fixture()
def _mock_deps():
    """Мокаем inbox_service, cost_analytics, swarm_artifact_store."""
    with (
        patch("src.core.weekly_digest.inbox_service") as mock_inbox,
        patch("src.core.weekly_digest.cost_analytics") as mock_cost,
        patch("src.core.weekly_digest.swarm_artifact_store") as mock_store,
    ):
        mock_inbox.upsert_item.return_value = {"item_id": "test-123"}
        mock_inbox.build_identity.return_value = MagicMock()
        mock_cost.get_monthly_cost_usd.return_value = 1.5
        mock_store.list_artifacts.return_value = []
        # list_items для inbox_data
        mock_inbox.list_items.return_value = []
        yield mock_inbox, mock_cost, mock_store


@pytest.mark.asyncio
async def test_telegram_callback_called_after_digest(_mock_deps) -> None:
    """Callback вызывается после успешного generate_digest."""
    mock_inbox, _, _ = _mock_deps
    svc = WeeklyDigestService()
    cb = AsyncMock()
    svc.set_telegram_callback(cb)

    result = await svc.generate_digest()

    assert result["ok"] is True
    cb.assert_awaited_once()
    # callback получает body (строку markdown)
    args = cb.call_args
    assert isinstance(args[0][0], str)  # первый аргумент — body


@pytest.mark.asyncio
async def test_digest_works_without_callback(_mock_deps) -> None:
    """Без установленного callback digest работает нормально."""
    svc = WeeklyDigestService()
    # Не вызываем set_telegram_callback
    result = await svc.generate_digest()
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_callback_error_does_not_break_digest(_mock_deps) -> None:
    """Ошибка в callback не ломает основной digest flow."""
    svc = WeeklyDigestService()
    cb = AsyncMock(side_effect=RuntimeError("Telegram API error"))
    svc.set_telegram_callback(cb)

    result = await svc.generate_digest()

    # digest должен быть ок несмотря на ошибку в callback
    assert result["ok"] is True
    cb.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_telegram_callback_replaces_previous(_mock_deps) -> None:
    """Повторный set_telegram_callback заменяет предыдущий callback."""
    svc = WeeklyDigestService()
    cb1 = AsyncMock()
    cb2 = AsyncMock()
    svc.set_telegram_callback(cb1)
    svc.set_telegram_callback(cb2)

    await svc.generate_digest()

    cb1.assert_not_awaited()
    cb2.assert_awaited_once()
