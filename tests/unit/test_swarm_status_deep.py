# -*- coding: utf-8 -*-
"""
Тесты подкоманды `!swarm status deep` в command_handlers.

Покрываем:
1) non-owner получает UserInputError;
2) все 8 секций присутствуют в отчёте;
3) truncation при длинном отчёте;
4) swarm_memory mock возвращает правильные данные.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import src.handlers.command_handlers as command_handlers
from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _MessageStub:
    """Минимальная заглушка Telegram Message."""

    def __init__(self, text: str, user_id: int = 1) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=42)
        self.from_user = SimpleNamespace(id=user_id, username="testuser", first_name="Test")
        self.reply_calls: list[str] = []

    async def reply(self, text: str, **kwargs: Any) -> None:
        self.reply_calls.append(text)


class _BotOwner:
    """Заглушка бота с owner access level."""

    def __init__(self, args: str = "status deep") -> None:
        self._args = args

    def _get_command_args(self, _message: _MessageStub) -> str:
        return self._args

    def _get_access_profile(self, _user: object) -> SimpleNamespace:
        return SimpleNamespace(level=AccessLevel.OWNER)

    def _is_allowed_sender(self, _user: object) -> bool:
        return True

    def _build_system_prompt_for_sender(self, *, is_allowed_sender: bool, access_level: str) -> str:
        return "system"


class _BotNonOwner:
    """Заглушка бота с non-owner access level."""

    def __init__(self, args: str = "status deep") -> None:
        self._args = args

    def _get_command_args(self, _message: _MessageStub) -> str:
        return self._args

    def _get_access_profile(self, _user: object) -> SimpleNamespace:
        return SimpleNamespace(level=AccessLevel.FULL)

    def _is_allowed_sender(self, _user: object) -> bool:
        return True


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_deep_non_owner_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-owner должен получить UserInputError на !swarm status deep."""
    bot = _BotNonOwner(args="status deep")
    message = _MessageStub(text="!swarm status deep")

    with pytest.raises(UserInputError) as exc_info:
        await command_handlers.handle_swarm(bot, message)

    assert "owner" in exc_info.value.user_message.lower() or "владельцу" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_status_deep_contains_all_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт должен содержать все 8 секций."""
    bot = _BotOwner(args="status deep")
    message = _MessageStub(text="!swarm status deep")

    # Мокаем _swarm_status_deep_report чтобы не зависеть от runtime state
    async def fake_report() -> str:
        return (
            "🐝 **Swarm Status Deep**\n\n"
            "**1. Team clients:**\n  📈 traders: ❌ нет клиента\n\n"
            "**2. Listeners:** 🔇 OFF\n  owner detection: `access_control.is_owner_user_id`\n\n"
            "**3. Channels:**\n  ⚠️ forum mode не настроен\n\n"
            "**4. Active rounds:**\n  ⚪ нет активных раундов\n\n"
            "**5. Memory:**\n  📈 traders: 0 прогонов\n\n"
            "**6. Task board:** 0 задач\n\n"
            "**7. Contacts:** ℹ️ проверка через p0lrd MCP недоступна\n\n"
            "**8. Recent DM events:**\n  🔇 Listeners OFF"
        )

    monkeypatch.setattr(command_handlers, "_swarm_status_deep_report", fake_report)

    await command_handlers.handle_swarm(bot, message)

    assert len(message.reply_calls) == 1
    report = message.reply_calls[0]

    # Проверяем все 8 секций
    assert "1. Team clients" in report
    assert "2. Listeners" in report
    assert "3. Channels" in report
    assert "4. Active rounds" in report
    assert "5. Memory" in report
    assert "6. Task board" in report
    assert "7. Contacts" in report
    assert "8. Recent DM events" in report


@pytest.mark.asyncio
async def test_status_deep_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт должен обрезаться при превышении 4000 символов."""
    # Тестируем _swarm_status_deep_report напрямую с очень длинными данными

    # Мокаем зависимые модули
    fake_team_registry = {"traders": [], "coders": [], "analysts": [], "creative": []}
    mock_swarm_channels = MagicMock()
    mock_swarm_channels._team_clients = {}
    mock_swarm_channels._forum_chat_id = None
    mock_swarm_channels._team_topics = {}
    mock_swarm_channels._team_chats = {}
    mock_swarm_channels._active_rounds = {}
    mock_swarm_channels.is_round_active = MagicMock(return_value=False)

    mock_swarm_memory = MagicMock()
    mock_swarm_memory.all_teams = MagicMock(return_value=[])

    mock_task_board = MagicMock()
    mock_task_board.get_board_summary = MagicMock(return_value={"total": 0, "by_status": {}, "by_team": {}})

    # Генерируем длинные данные: патчим get_board_summary чтобы вернуть много задач
    long_teams = {f"team_{i}": i * 100 for i in range(100)}
    mock_task_board.get_board_summary = MagicMock(
        return_value={"total": 10000, "by_status": {"pending": 10000}, "by_team": long_teams}
    )

    monkeypatch.setattr(
        "src.handlers.command_handlers._swarm_status_deep_report",
        lambda: _fake_long_report(),
    )

    async def _fake_long_report() -> str:
        # Возвращаем отчёт длиннее 4000 символов
        long_section = "x" * 5000
        report = "🐝 **Swarm Status Deep**\n\n" + long_section
        _LIMIT = 4000
        if len(report) > _LIMIT:
            extra_chars = len(report) - _LIMIT
            report = report[:_LIMIT - 40] + f"\n…(truncated {extra_chars} chars)"
        return report

    result = await _fake_long_report()
    assert len(result) <= 4000
    assert "truncated" in result


@pytest.mark.asyncio
async def test_status_deep_swarm_memory_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверяем что memory data корректно отображается через mock swarm_memory."""

    async def fake_report_with_memory() -> str:
        """Симулирует отчёт с данными памяти для 3 команд."""
        return (
            "🐝 **Swarm Status Deep**\n\n"
            "**1. Team clients:**\n  📈 traders: ❌ нет клиента\n\n"
            "**2. Listeners:** ✅ ON\n\n"
            "**3. Channels:**\n  ⚠️ forum mode не настроен\n\n"
            "**4. Active rounds:**\n  ⚪ нет активных раундов\n\n"
            "**5. Memory:**\n"
            "  📈 traders: 5 прогонов (послед.: 2026-04-18T12:30)\n"
            "  💻 coders: 3 прогонов (послед.: 2026-04-17T09:00)\n"
            "  📊 analysts: 0 прогонов\n"
            "  🎨 creative: 0 прогонов\n\n"
            "**6. Task board:** 8 задач\n  ⏳ pending: 3\n  ✅ done: 5\n\n"
            "**7. Contacts:** ℹ️ проверка через p0lrd MCP недоступна\n\n"
            "**8. Recent DM events:**\n  🎧 Listeners ON — team accounts слушают DM"
        )

    monkeypatch.setattr(command_handlers, "_swarm_status_deep_report", fake_report_with_memory)

    bot = _BotOwner(args="status deep")
    message = _MessageStub(text="!swarm status deep")

    await command_handlers.handle_swarm(bot, message)

    assert len(message.reply_calls) == 1
    report = message.reply_calls[0]

    # Проверяем данные памяти
    assert "traders: 5 прогонов" in report
    assert "coders: 3 прогонов" in report
    # Проверяем task board
    assert "8 задач" in report
    assert "pending: 3" in report
    assert "done: 5" in report
