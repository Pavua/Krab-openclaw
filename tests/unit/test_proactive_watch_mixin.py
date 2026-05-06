# -*- coding: utf-8 -*-
"""Wave 31-K tests: ProactiveWatchMixin extraction."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_proactive_watch_mixin_importable():
    from src.userbot.proactive_watch import ProactiveWatchMixin

    assert ProactiveWatchMixin.__name__ == "ProactiveWatchMixin"


def test_kraab_userbot_inherits_proactive_watch_mixin():
    from src.userbot.proactive_watch import ProactiveWatchMixin
    from src.userbot_bridge import KraabUserbot

    assert ProactiveWatchMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_send_proactive_watch_alert",
        "_ensure_proactive_watch_started",
        "_run_proactive_watch_loop",
    ],
)
def test_methods_resolve_via_mixin(method_name):
    from src.userbot.proactive_watch import ProactiveWatchMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in ProactiveWatchMixin.__dict__
    assert method_name not in KraabUserbot.__dict__


@pytest.mark.asyncio
async def test_send_alert_via_userbot_when_connected(monkeypatch):
    """client.is_connected → отправка через userbot, не reserve_bot."""
    from src.userbot.proactive_watch import ProactiveWatchMixin

    bot = ProactiveWatchMixin.__new__(ProactiveWatchMixin)
    bot.client = MagicMock()
    bot.client.is_connected = True
    bot.client.send_message = AsyncMock()
    bot._owner_notify_target = 12345
    bot._split_message = lambda x: [x]

    await bot._send_proactive_watch_alert("hello")

    bot.client.send_message.assert_awaited_once_with(12345, "hello")


@pytest.mark.asyncio
async def test_send_alert_via_reserve_bot_fallback(monkeypatch):
    """userbot offline + reserve_bot.is_running → fallback."""
    from src.userbot import proactive_watch as pw
    from src.userbot.proactive_watch import ProactiveWatchMixin

    fake_reserve = MagicMock()
    fake_reserve.is_running = True
    fake_reserve.send_to_owner = AsyncMock()
    monkeypatch.setattr(pw, "reserve_bot", fake_reserve)

    bot = ProactiveWatchMixin.__new__(ProactiveWatchMixin)
    bot.client = None  # userbot offline

    await bot._send_proactive_watch_alert("emergency")

    fake_reserve.send_to_owner.assert_awaited_once_with("[reserve] emergency")


@pytest.mark.asyncio
async def test_send_alert_raises_when_both_unavailable(monkeypatch):
    """Оба недоступны → RuntimeError для caller."""
    from src.userbot import proactive_watch as pw
    from src.userbot.proactive_watch import ProactiveWatchMixin

    fake_reserve = MagicMock()
    fake_reserve.is_running = False
    monkeypatch.setattr(pw, "reserve_bot", fake_reserve)

    bot = ProactiveWatchMixin.__new__(ProactiveWatchMixin)
    bot.client = None

    with pytest.raises(RuntimeError, match="telegram_client_not_ready"):
        await bot._send_proactive_watch_alert("test")


def test_run_loop_is_coroutine():
    from src.userbot.proactive_watch import ProactiveWatchMixin

    assert inspect.iscoroutinefunction(ProactiveWatchMixin._run_proactive_watch_loop)
