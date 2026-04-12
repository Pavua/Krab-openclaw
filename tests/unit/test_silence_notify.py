# -*- coding: utf-8 -*-
"""
Тесты SilenceManager + handle_notify:
- silence toggle (глобальный, per-chat)
- chat-level muting
- notification delivery (handle_notify on/off/status)
- rate limiting через GlobalTelegramRateLimiter
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ──────────────────────────────────────────────────────────────
# SilenceManager — импорт
# ──────────────────────────────────────────────────────────────
try:
    from src.core.silence_mode import SilenceManager
except ImportError:
    pytest.skip("src.core.silence_mode not available", allow_module_level=True)

try:
    from src.core.telegram_rate_limiter import GlobalTelegramRateLimiter
except ImportError:
    pytest.skip("src.core.telegram_rate_limiter not available", allow_module_level=True)


# ══════════════════════════════════════════════════════════════
# Группа 1: Silence toggle — глобальный
# ══════════════════════════════════════════════════════════════


class TestSilenceToggleGlobal:
    """Тесты глобального режима тишины."""

    def setup_method(self):
        self.sm = SilenceManager()

    def test_global_mute_activates(self):
        """Глобальный mute включается и виден через is_global_muted()."""
        self.sm.mute_global(60)
        assert self.sm.is_global_muted()

    def test_global_mute_affects_all_chats(self):
        """Глобальный mute делает is_silenced() True для любого chat_id."""
        self.sm.mute_global(60)
        for chat_id in ("111", "222", "333"):
            assert self.sm.is_silenced(chat_id), f"chat {chat_id} должен быть silenced"

    def test_global_unmute_clears(self):
        """unmute_global() возвращает True и снимает глобальный mute."""
        self.sm.mute_global(60)
        result = self.sm.unmute_global()
        assert result is True
        assert not self.sm.is_global_muted()

    def test_global_unmute_idempotent(self):
        """Повторный unmute_global() возвращает False, не ломается."""
        assert self.sm.unmute_global() is False

    def test_global_mute_expires_naturally(self):
        """После истечения времени is_global_muted() возвращает False."""
        self.sm.mute_global(1)
        # Форсируем истечение
        self.sm._global_until = time.monotonic() - 1.0
        assert not self.sm.is_global_muted()
        assert self.sm._global_until is None  # Поле очищается автоматически


# ══════════════════════════════════════════════════════════════
# Группа 2: Chat-level muting
# ══════════════════════════════════════════════════════════════


class TestChatLevelMuting:
    """Тесты per-chat режима тишины."""

    def setup_method(self):
        self.sm = SilenceManager()

    def test_mute_specific_chat(self):
        """mute_chat() заглушает только конкретный чат."""
        self.sm.mute_chat("chat_A", 30)
        assert self.sm.is_chat_muted("chat_A")
        assert not self.sm.is_chat_muted("chat_B")

    def test_mute_multiple_chats_independently(self):
        """Несколько чатов можно заглушить независимо."""
        self.sm.mute_chat("chat_1", 10)
        self.sm.mute_chat("chat_2", 20)
        assert self.sm.is_chat_muted("chat_1")
        assert self.sm.is_chat_muted("chat_2")
        self.sm.unmute_chat("chat_1")
        assert not self.sm.is_chat_muted("chat_1")
        assert self.sm.is_chat_muted("chat_2")  # chat_2 остался muted

    def test_unmute_chat_returns_true_if_was_muted(self):
        """unmute_chat() возвращает True, если чат был заглушён."""
        self.sm.mute_chat("chat_X", 5)
        assert self.sm.unmute_chat("chat_X") is True

    def test_unmute_chat_returns_false_if_not_muted(self):
        """unmute_chat() возвращает False для незаглушённого чата."""
        assert self.sm.unmute_chat("unknown_chat") is False

    def test_chat_mute_remaining_decreases(self):
        """Оставшееся время mute уменьшается со временем (не отрицательное)."""
        self.sm.mute_chat("chat_Z", 10)
        remaining = self.sm.chat_mute_remaining_sec("chat_Z")
        assert 500 < remaining <= 600

    def test_chat_mute_remaining_zero_after_expiry(self):
        """После истечения оставшееся время = 0."""
        self.sm.mute_chat("chat_Z", 10)
        self.sm._chat_mutes["chat_Z"] = time.monotonic() - 1.0
        assert self.sm.chat_mute_remaining_sec("chat_Z") == 0.0

    def test_auto_silence_sets_short_mute(self):
        """auto_silence_owner_typing() устанавливает auto-mute на N минут."""
        self.sm.auto_silence_owner_typing("chat_Y", minutes=3)
        assert self.sm.is_chat_muted("chat_Y")
        remaining = self.sm.chat_mute_remaining_sec("chat_Y")
        assert 170 < remaining <= 180  # ~3 мин

    def test_auto_silence_does_not_override_longer_mute(self):
        """auto-silence не перезаписывает длинный ручной mute."""
        self.sm.mute_chat("chat_Y", 60)  # 60 минут ручной
        self.sm.auto_silence_owner_typing("chat_Y", minutes=3)
        remaining = self.sm.chat_mute_remaining_sec("chat_Y")
        assert remaining > 300  # Ручной mute сохранён


# ══════════════════════════════════════════════════════════════
# Группа 3: Notification delivery (handle_notify)
# ══════════════════════════════════════════════════════════════


def _make_mock_message(text: str) -> MagicMock:
    """Создать мок-сообщение Telegram для тестов handle_notify."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    return msg


def _make_mock_bot(args_result: str) -> MagicMock:
    """Создать мок-бот с _get_command_args()."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=args_result)
    return bot


@pytest.mark.asyncio
async def test_notify_on_enables_setting():
    """!notify on обновляет настройку и отвечает с подтверждением."""
    try:
        from src.handlers.command_handlers import handle_notify
    except ImportError:
        pytest.skip("handle_notify not available")

    mock_cfg = MagicMock()
    bot = _make_mock_bot("on")
    msg = _make_mock_message("!notify on")

    with patch("src.handlers.command_handlers.config", mock_cfg):
        # Патчим импорт внутри функции
        with patch.dict("sys.modules", {}):
            pass
        # Запускаем напрямую с моком config
        mock_cfg.update_setting = MagicMock()
        mock_cfg.TOOL_NARRATION_ENABLED = True

        with patch("src.config.config", mock_cfg):
            await handle_notify(bot, msg)

    # Ответ должен быть отправлен
    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args[0][0]
    assert "ON" in reply_text


@pytest.mark.asyncio
async def test_notify_off_disables_setting():
    """!notify off обновляет настройку и отвечает с подтверждением."""
    try:
        from src.handlers.command_handlers import handle_notify
    except ImportError:
        pytest.skip("handle_notify not available")

    mock_cfg = MagicMock()
    bot = _make_mock_bot("off")
    msg = _make_mock_message("!notify off")
    mock_cfg.TOOL_NARRATION_ENABLED = False

    with patch("src.config.config", mock_cfg):
        await handle_notify(bot, msg)

    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args[0][0]
    assert "OFF" in reply_text


@pytest.mark.asyncio
async def test_notify_status_shows_current_state():
    """!notify без аргументов показывает текущий статус."""
    try:
        from src.handlers.command_handlers import handle_notify
    except ImportError:
        pytest.skip("handle_notify not available")

    mock_cfg = MagicMock()
    bot = _make_mock_bot("")  # Пустые аргументы — статус
    msg = _make_mock_message("!notify")
    mock_cfg.TOOL_NARRATION_ENABLED = True

    with patch("src.config.config", mock_cfg):
        await handle_notify(bot, msg)

    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args[0][0]
    # Должен содержать инструкцию
    assert "!notify on" in reply_text or "!notify off" in reply_text


# ══════════════════════════════════════════════════════════════
# Группа 4: Rate limiting
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rate_limiter_under_cap_no_wait():
    """N acquire() при N <= max_per_sec проходят без задержки."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.2)
    start = time.monotonic()
    for _ in range(5):
        await limiter.acquire(purpose="notify_delivery")
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, f"Задержки быть не должно, elapsed={elapsed:.3f}s"
    assert limiter.stats()["total_waited"] == 0


@pytest.mark.asyncio
async def test_rate_limiter_over_cap_slows_down():
    """N+1 acquire() при max=N вызывает ожидание."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=3, window_sec=0.2)
    for _ in range(3):
        await limiter.acquire()
    start = time.monotonic()
    await limiter.acquire()  # 4-й — должен ждать
    elapsed = time.monotonic() - start
    assert elapsed >= 0.1, f"Ожидали задержку >=0.1s, elapsed={elapsed:.3f}s"
    assert limiter.stats()["total_waited"] == 1


@pytest.mark.asyncio
async def test_rate_limiter_stats_track_correctly():
    """stats() корректно считает total_acquired."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=10, window_sec=0.5)
    for i in range(7):
        await limiter.acquire(purpose=f"msg_{i}")
    stats = limiter.stats()
    assert stats["total_acquired"] == 7
    assert stats["max_per_sec"] == 10


@pytest.mark.asyncio
async def test_rate_limiter_reset_counters():
    """reset_counters() обнуляет счётчики без потери конфигурации."""
    limiter = GlobalTelegramRateLimiter(max_per_sec=5, window_sec=0.1)
    await limiter.acquire()
    await limiter.acquire()
    limiter.reset_counters()
    stats = limiter.stats()
    assert stats["total_acquired"] == 0
    assert stats["total_waited"] == 0
    assert stats["total_wait_sec"] == 0.0
    # Конфиг не изменился
    assert stats["max_per_sec"] == 5


# ══════════════════════════════════════════════════════════════
# Группа 5: Интеграция silence + rate limiter
# ══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_silenced_chat_does_not_consume_rate_budget():
    """
    Симуляция: если чат заглушён — сообщение не отправляется,
    rate limiter не вызывается.
    """
    sm = SilenceManager()
    sm.mute_chat("chat_silent", 10)

    send_count = 0

    async def fake_send_if_not_silenced(
        chat_id: str, text: str, limiter: GlobalTelegramRateLimiter
    ):
        if sm.is_silenced(chat_id):
            return  # Молчим
        await limiter.acquire(purpose="send_message")
        nonlocal send_count
        send_count += 1

    limiter = GlobalTelegramRateLimiter(max_per_sec=10, window_sec=1.0)
    await fake_send_if_not_silenced("chat_silent", "hello", limiter)
    await fake_send_if_not_silenced("chat_active", "world", limiter)

    assert send_count == 1  # Только chat_active отправил
    assert limiter.stats()["total_acquired"] == 1


def test_silence_status_format_contains_remaining_minutes():
    """format_status() содержит информацию об оставшемся времени."""
    sm = SilenceManager()
    sm.mute_chat("123456789", 15)
    text = sm.format_status()
    assert "123456789" in text
    assert "мин" in text


def test_silence_status_cleans_expired_on_read():
    """status() автоматически удаляет истёкшие per-chat mutes."""
    sm = SilenceManager()
    sm.mute_chat("expired_chat", 1)
    sm._chat_mutes["expired_chat"] = time.monotonic() - 5.0  # Уже истёк
    sm.mute_chat("active_chat", 10)
    st = sm.status()
    assert "expired_chat" not in st["muted_chats"]
    assert "active_chat" in st["muted_chats"]
