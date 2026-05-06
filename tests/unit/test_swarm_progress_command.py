# -*- coding: utf-8 -*-
"""
Тесты для !swarm progress команды (Wave 33-B).

Покрывает:
- Нет активных rooms → сообщение "all idle"
- 1 активная комната → прогресс + ETA
- Несколько комнат → все перечислены
- Edge case: rounds_done=0 → ETA="?"
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_bus import SwarmProgressRegistry, SwarmRoomSession

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_session(
    team: str,
    topic: str,
    rounds_total: int,
    rounds_completed: int = 0,
    elapsed_sec: float = 60.0,
) -> SwarmRoomSession:
    """Создаёт SwarmRoomSession с нужным elapsed временем."""
    sess = SwarmRoomSession(
        team=team,
        topic=topic,
        rounds_total=rounds_total,
        rounds_completed=rounds_completed,
        started_at=time.monotonic() - elapsed_sec,
    )
    return sess


def _make_message_reply() -> AsyncMock:
    """Мок pyrogram message с async reply."""
    msg = MagicMock()
    msg.reply = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# Тесты SwarmProgressRegistry
# ---------------------------------------------------------------------------


class TestSwarmProgressRegistry:
    """Unit-тесты реестра прогресса (без swarm_commands handler)."""

    def setup_method(self) -> None:
        self.registry = SwarmProgressRegistry()

    def test_start_session_creates_entry(self) -> None:
        sid = self.registry.start_session(team="coders", topic="рефакторинг", rounds_total=3)
        assert sid in self.registry.get_all_active()
        sess = self.registry.get_all_active()[sid]
        assert sess.team == "coders"
        assert sess.topic == "рефакторинг"
        assert sess.rounds_total == 3
        assert sess.rounds_completed == 0

    def test_record_round_done_increments(self) -> None:
        sid = self.registry.start_session(team="analysts", topic="BTC", rounds_total=2)
        self.registry.record_round_done(sid)
        sess = self.registry.get_all_active()[sid]
        assert sess.rounds_completed == 1

    def test_end_session_removes_entry(self) -> None:
        sid = self.registry.start_session(team="traders", topic="тест", rounds_total=1)
        self.registry.end_session(sid)
        assert sid not in self.registry.get_all_active()

    def test_active_count(self) -> None:
        assert self.registry.active_count() == 0
        sid1 = self.registry.start_session(team="traders", topic="a", rounds_total=1)
        sid2 = self.registry.start_session(team="coders", topic="b", rounds_total=2)
        assert self.registry.active_count() == 2
        self.registry.end_session(sid1)
        assert self.registry.active_count() == 1
        self.registry.end_session(sid2)
        assert self.registry.active_count() == 0

    def test_eta_sec_no_data_when_rounds_done_zero(self) -> None:
        """ETA недоступен если ни одного раунда не завершено."""
        sess = _make_session("coders", "тест", rounds_total=3, rounds_completed=0)
        assert sess.eta_sec() is None

    def test_eta_sec_calculated_when_rounds_done(self) -> None:
        """ETA вычисляется на основе среднего времени раунда."""
        # 1 раунд занял 60 сек, осталось 2 раунда → ETA ~120 сек
        sess = _make_session(
            "analysts", "BTC", rounds_total=3, rounds_completed=1, elapsed_sec=60.0
        )
        eta = sess.eta_sec()
        assert eta is not None
        assert 100 < eta < 140  # допускаем погрешность ±20%

    def test_eta_sec_none_when_all_done(self) -> None:
        """ETA=None если раунды уже все завершены."""
        sess = _make_session("traders", "тест", rounds_total=2, rounds_completed=2, elapsed_sec=90)
        assert sess.eta_sec() is None

    def test_record_round_done_unknown_sid_is_noop(self) -> None:
        """Несуществующий sid не роняет реестр."""
        self.registry.record_round_done("nonexistent-sid")  # не должен бросать

    def test_end_session_unknown_sid_is_noop(self) -> None:
        """Несуществующий sid при end_session не роняет реестр."""
        self.registry.end_session("nonexistent-sid")  # не должен бросать


# ---------------------------------------------------------------------------
# Тесты !swarm progress handler (через прямой вызов логики)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_no_active_rooms() -> None:
    """Нет активных rooms → сообщение 'all idle'."""
    from src.handlers.commands import swarm_commands

    bot = MagicMock()
    bot._get_command_args.return_value = "progress"
    message = _make_message_reply()

    empty_registry = SwarmProgressRegistry()
    # Патчим singleton в swarm_bus (именно там handler его импортирует)
    with patch("src.core.swarm_bus.swarm_progress", empty_registry):
        await swarm_commands.handle_swarm(bot, message)

    reply_text = message.reply.call_args[0][0]
    assert "idle" in reply_text.lower() or "swarm" in reply_text.lower()


@pytest.mark.asyncio
async def test_progress_one_active_room_with_eta() -> None:
    """1 активная комната → показывает прогресс + ETA."""
    from src.handlers.commands import swarm_commands

    registry = SwarmProgressRegistry()
    sid = registry.start_session(team="coders", topic="рефакторинг модуля", rounds_total=3)
    # Симулируем 1 завершённый раунд за 30 сек
    sess = registry.get_all_active()[sid]
    sess.rounds_completed = 1
    sess.started_at = time.monotonic() - 30.0

    bot = MagicMock()
    bot._get_command_args.return_value = "progress"
    message = _make_message_reply()

    with patch("src.core.swarm_bus.swarm_progress", registry):
        await swarm_commands.handle_swarm(bot, message)

    reply_text = message.reply.call_args[0][0]
    assert "coders" in reply_text
    assert "рефакторинг" in reply_text
    # Прогресс-бар или счётчик раундов
    assert "1/3" in reply_text
    # ETA должен быть указан (не "?")
    assert "ETA" in reply_text or "eta" in reply_text.lower()


@pytest.mark.asyncio
async def test_progress_multiple_rooms_all_listed() -> None:
    """Несколько активных комнат → все перечислены в ответе."""
    from src.handlers.commands import swarm_commands

    registry = SwarmProgressRegistry()
    registry.start_session(team="traders", topic="анализ BTC", rounds_total=2)
    registry.start_session(team="analysts", topic="трейды 2026", rounds_total=5)
    registry.start_session(team="creative", topic="копирайтинг", rounds_total=1)

    bot = MagicMock()
    bot._get_command_args.return_value = "progress"
    message = _make_message_reply()

    with patch("src.core.swarm_bus.swarm_progress", registry):
        await swarm_commands.handle_swarm(bot, message)

    reply_text = message.reply.call_args[0][0]
    assert "traders" in reply_text
    assert "analysts" in reply_text
    assert "creative" in reply_text


@pytest.mark.asyncio
async def test_progress_eta_unknown_when_rounds_done_zero() -> None:
    """rounds_done=0 → ETA='?'."""
    from src.handlers.commands import swarm_commands

    registry = SwarmProgressRegistry()
    sid = registry.start_session(team="coders", topic="тест zero", rounds_total=4)
    sess = registry.get_all_active()[sid]
    sess.rounds_completed = 0
    sess.started_at = time.monotonic() - 10.0

    bot = MagicMock()
    bot._get_command_args.return_value = "progress"
    message = _make_message_reply()

    with patch("src.core.swarm_bus.swarm_progress", registry):
        await swarm_commands.handle_swarm(bot, message)

    reply_text = message.reply.call_args[0][0]
    # ETA должен быть неизвестен
    assert "?" in reply_text
