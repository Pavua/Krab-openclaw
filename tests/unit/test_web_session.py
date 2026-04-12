# -*- coding: utf-8 -*-
"""
Тесты для src/web_session.py — WebSessionManager.
Все Playwright-операции замокированы, IO не происходит.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager():
    """Создаём WebSessionManager без реального Playwright."""
    # Импортируем здесь, чтобы мок на config применился раньше
    from src.web_session import WebSessionManager

    return WebSessionManager()


# ---------------------------------------------------------------------------
# Начальное состояние
# ---------------------------------------------------------------------------


def test_initial_state(manager):
    """Менеджер создаётся в неактивном состоянии."""
    assert manager.is_active is False
    assert manager.playwright is None
    assert manager.context is None
    assert manager.page is None


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_sets_is_active(manager):
    """start() устанавливает is_active=True при успешном запуске."""
    mock_page = AsyncMock()
    mock_context = AsyncMock()
    mock_context.pages = [mock_page]

    mock_playwright = AsyncMock()
    mock_playwright.chromium.launch_persistent_context = AsyncMock(return_value=mock_context)

    mock_pw_instance = AsyncMock()
    mock_pw_instance.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_pw_instance.__aexit__ = AsyncMock(return_value=False)

    with patch("src.web_session.async_playwright") as mock_apw:
        # async_playwright() возвращает async context-manager → .start() возвращает объект
        mock_apw.return_value.start = AsyncMock(return_value=mock_playwright)

        await manager.start()

    assert manager.is_active is True


@pytest.mark.asyncio
async def test_start_failure_does_not_raise(manager):
    """start() при исключении не падает наружу — is_active остаётся False."""
    with patch("src.web_session.async_playwright", side_effect=RuntimeError("pw fail")):
        await manager.start()  # не должен кидать исключение

    assert manager.is_active is False


@pytest.mark.asyncio
async def test_stop_resets_state(manager):
    """stop() закрывает context/playwright и сбрасывает is_active."""
    mock_context = AsyncMock()
    mock_playwright = AsyncMock()

    manager.context = mock_context
    manager.playwright = mock_playwright
    manager.is_active = True

    await manager.stop()

    mock_context.close.assert_awaited_once()
    mock_playwright.stop.assert_awaited_once()
    assert manager.is_active is False


@pytest.mark.asyncio
async def test_stop_when_not_started(manager):
    """stop() без предыдущего start() не падает."""
    await manager.stop()  # context=None, playwright=None — всё ок
    assert manager.is_active is False


# ---------------------------------------------------------------------------
# take_screenshot()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_take_screenshot_returns_empty_when_inactive(manager):
    """take_screenshot() возвращает пустую строку если сессия неактивна."""
    result = await manager.take_screenshot()
    assert result == ""


@pytest.mark.asyncio
async def test_take_screenshot_returns_path(manager, tmp_path):
    """take_screenshot() возвращает путь к файлу при активной сессии."""
    mock_page = AsyncMock()
    manager.page = mock_page
    manager.is_active = True

    with patch("src.web_session.os.getcwd", return_value=str(tmp_path)):
        result = await manager.take_screenshot("test.png")

    assert result == str(tmp_path / "test.png")
    mock_page.screenshot.assert_awaited_once()


@pytest.mark.asyncio
async def test_take_screenshot_on_exception_returns_empty(manager):
    """take_screenshot() возвращает пустую строку при ошибке Playwright."""
    mock_page = AsyncMock()
    mock_page.screenshot.side_effect = Exception("screenshot fail")
    manager.page = mock_page
    manager.is_active = True

    result = await manager.take_screenshot()
    assert result == ""


# ---------------------------------------------------------------------------
# chatgpt_query() — только ветка «сессия не активна → должна запуститься»
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatgpt_query_returns_error_on_selector_timeout(manager):
    """chatgpt_query() возвращает строку с ошибкой если ChatGPT не загрузился."""
    mock_page = AsyncMock()
    mock_page.wait_for_selector.side_effect = Exception("timeout")

    manager.page = mock_page
    manager.is_active = True

    result = await manager.chatgpt_query("hello")
    assert "ChatGPT" in result or "❌" in result


# ---------------------------------------------------------------------------
# login_mode()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_mode_returns_message(manager):
    """login_mode() возвращает сообщение о старте браузера."""
    # Мокаем stop() и start() чтобы не трогать реальный Playwright
    manager.stop = AsyncMock()
    manager.start = AsyncMock()

    result = await manager.login_mode()

    manager.stop.assert_awaited_once()
    manager.start.assert_awaited_once_with(headless=False)
    assert isinstance(result, str)
    assert len(result) > 0
