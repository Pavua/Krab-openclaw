# -*- coding: utf-8 -*-
"""
Регрессии `!stats` owner-команды (handle_stats + _render_stats_panel).

Что тестируем:

1. **Happy path** — пустые singleton'ы дают панель со всеми четырьмя секциями
   (rate limiter / chat ban cache / chat capability cache / silence mode)
   и голосовым runtime-профилем.
2. **Populated caches** — когда в chat_ban_cache лежит одна запись и в
   chat_capability_cache одна с `voice_allowed=False`, цифры в панели
   соответствуют реальному состоянию.
3. **Rate limiter с waits** — после принудительного acquire'а сверх cap
   счётчики `total_waited` / `total_acquired` попадают в панель.

Каждый тест изолирует module-level singleton'ы через fixture, иначе один
тест может протекать в другой (например, chat_ban_cache.list_entries() видит
запись от предыдущего теста).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.chat_ban_cache import chat_ban_cache
from src.core.chat_capability_cache import chat_capability_cache
from src.core.silence_mode import silence_manager
from src.core.telegram_rate_limiter import (
    GlobalTelegramRateLimiter,
    telegram_rate_limiter,
)
from src.handlers.command_handlers import _render_stats_panel, handle_stats
from src.userbot_bridge import KraabUserbot


@pytest.fixture(autouse=True)
def _reset_singletons(tmp_path: Path) -> None:
    """
    Сбрасывает module-level singleton'ы до пустого состояния перед каждым тестом.

    Паттерн совпадает с `test_telegram_rate_limiter.py` (для limiter) и
    `test_chat_capability_cache.py` (для cache через configure_default_path).
    Для silence_manager чистим внутренние dict'ы напрямую, т.к. модуль не
    предоставляет публичный reset (но он и не нужен в продакшене — только в тестах).
    """
    # Telegram rate limiter: очищаем окно и счётчики.
    telegram_rate_limiter._recent.clear()
    telegram_rate_limiter.reset_counters()
    telegram_rate_limiter.configure(max_per_sec=20, window_sec=1.0)

    # Chat ban cache: redirect на tmp-файл и очистить in-memory.
    chat_ban_cache.configure_default_path(tmp_path / "chat_ban_cache.json")

    # Chat capability cache: то же — redirect + очистка.
    chat_capability_cache.configure_default_path(tmp_path / "chat_capability_cache.json")

    # Silence manager: чистим per-chat и глобальный mute.
    silence_manager._chat_mutes.clear()
    silence_manager._global_until = None

    yield


def _make_bot_stub() -> KraabUserbot:
    """
    Минимальный bot stub для !stats — без Pyrogram client'а.

    `get_voice_runtime_profile()` читает поля `voice_mode`, `voice_reply_*`,
    `perceptor` напрямую через `getattr` с дефолтами, поэтому достаточно
    выставить только их. Паттерн из `tests/unit/test_userbot_voice_blocklist.py`
    и `tests/unit/test_userbot_capability_truth.py`.
    """
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.voice_mode = False
    bot.voice_reply_speed = 1.5
    bot.voice_reply_voice = "ru-RU-DmitryNeural"
    bot.voice_reply_delivery = "text+voice"
    bot.perceptor = None
    # Атрибуты для расширенной секции !stats (uptime, счётчик сообщений).
    import time as _t

    bot._session_start_time = _t.time()
    bot._session_messages_processed = 0
    return bot


def test_stats_panel_happy_path_renders_all_sections() -> None:
    """Пустые singleton'ы → все заголовки присутствуют, нулевые счётчики."""
    bot = _make_bot_stub()
    panel = _render_stats_panel(bot)

    # Заголовок и все четыре секции должны присутствовать.
    assert "Krab Stats" in panel
    assert "Telegram API rate limiter" in panel
    assert "Chat ban cache" in panel
    assert "Chat capability cache" in panel
    assert "Silence mode" in panel
    assert "Voice runtime" in panel

    # Нулевые значения и пустые cache'и.
    assert "`20 req/s`" in panel
    assert "(`0` active)" in panel  # chat ban cache
    assert "(`0` cached)" in panel  # chat capability cache
    assert "Voice запрещён явно: `0`" in panel
    assert "Slow mode > 0: `0`" in panel
    assert "Заглушённых чатов: `0`" in panel
    assert "Глобально: `ВЫКЛ`" in panel


def test_stats_panel_reflects_populated_caches() -> None:
    """
    Одна запись в chat_ban_cache и одна в chat_capability_cache (voice=False)
    должны попасть в счётчики панели.
    """
    chat_ban_cache.mark_banned(-1001587432709, "UserBannedInChannel")
    chat_capability_cache.upsert(
        -1001587432709,
        slow_mode_seconds=10,
        voice_allowed=False,
        text_allowed=True,
        chat_type="SUPERGROUP",
    )

    bot = _make_bot_stub()
    panel = _render_stats_panel(bot)

    # Chat ban cache: одна активная запись и preview с error code.
    assert "Chat ban cache** (`1` active)" in panel
    assert "-1001587432709" in panel
    assert "UserBannedInChannel" in panel

    # Chat capability cache: один cached, один voice-forbidden, один slow_mode>0.
    assert "Chat capability cache** (`1` cached)" in panel
    assert "Voice запрещён явно: `1`" in panel
    assert "Slow mode > 0: `1`" in panel


@pytest.mark.asyncio
async def test_stats_panel_shows_rate_limiter_waits() -> None:
    """
    После принудительного переполнения rate limiter (6 acquire'ов при cap=5),
    `total_waited` > 0 должно отразиться в панели.
    """
    # Используем локальный instance чтобы изолированно проверить stats(),
    # а потом инжектируем его метрики в глобальный singleton для renderer'а.
    local = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.2)
    for _ in range(5):
        await local.acquire(purpose="burst")
    await local.acquire(purpose="overflow")  # ← этот вызов спит

    local_stats = local.stats()
    assert local_stats["total_acquired"] == 6
    assert local_stats["total_waited"] == 1
    assert local_stats["total_wait_sec"] > 0

    # Переливаем в глобальный singleton через те же acquire'ы, чтобы renderer
    # читал реальный state (никаких приватных set'ов в renderer не делаем).
    telegram_rate_limiter.configure(max_per_sec=5, window_sec=0.2)
    for _ in range(5):
        await telegram_rate_limiter.acquire(purpose="burst")
    await telegram_rate_limiter.acquire(purpose="overflow")

    bot = _make_bot_stub()
    panel = _render_stats_panel(bot)

    assert "`5 req/s`" in panel
    assert "Всего acquire: `6`" in panel
    assert "ждали: `1`" in panel


class _FakeMessage:
    """Минимальный двойник pyrogram Message с фиксацией текста reply()."""

    def __init__(self, text: str = "!stats") -> None:
        # `!stats` без аргументов → _get_command_args вернёт "" и handle_stats
        # отрендерит панель (а не уйдёт в subcommand `ecosystem`).
        self.text = text
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)


@pytest.mark.asyncio
async def test_handle_stats_replies_with_panel() -> None:
    """handle_stats должен отправлять сгенерированную панель через message.reply()."""
    bot = _make_bot_stub()
    msg = _FakeMessage()
    await handle_stats(bot, msg)  # type: ignore[arg-type]
    assert len(msg.replies) == 1
    payload = msg.replies[0]
    assert "Krab Stats" in payload
    assert "Telegram API rate limiter" in payload
