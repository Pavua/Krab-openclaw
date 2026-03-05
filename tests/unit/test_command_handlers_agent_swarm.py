# -*- coding: utf-8 -*-
"""
Тесты команды `!agent swarm` в command_handlers.

Покрываем:
1) валидацию формата (тема обязательна);
2) успешный роевой раунд через AgentRoom + OpenClaw stream-адаптер.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.exceptions import UserInputError
import src.handlers.command_handlers as command_handlers


class _StatusMessage:
    """Минимальный async-объект сообщения-статуса для `.edit()`."""

    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit(self, text: str) -> None:
        self.edits.append(text)


class _MessageStub:
    """Минимальная заглушка Telegram Message для тестов command handler."""

    def __init__(self, text: str, chat_id: int = 123) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.reply_calls: list[str] = []
        self._status_messages: list[_StatusMessage] = []

    async def reply(self, text: str) -> _StatusMessage:
        self.reply_calls.append(text)
        status = _StatusMessage()
        self._status_messages.append(status)
        return status


class _BotStub:
    """Заглушка бота с минимальным API, которое ожидает handle_agent."""

    def __init__(self, args: str) -> None:
        self.current_role = "default"
        self._args = args

    def _get_command_args(self, _message: _MessageStub) -> str:
        return self._args


@pytest.mark.asyncio
async def test_agent_swarm_requires_topic() -> None:
    """`!agent swarm` без темы должен вернуть понятную user-ошибку."""
    bot = _BotStub(args="swarm")
    message = _MessageStub(text="!agent swarm")

    with pytest.raises(UserInputError):
        await command_handlers.handle_agent(bot, message)


@pytest.mark.asyncio
async def test_agent_swarm_runs_three_role_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Роевой раунд выполняет 3 вызова stream и формирует итоговый ответ."""
    calls: list[dict[str, str]] = []

    async def fake_send_message_stream(
        message: str,
        chat_id: str,
        system_prompt: str | None = None,
        force_cloud: bool = False,
        max_output_tokens: int | None = None,
        images=None,
    ):
        del force_cloud, max_output_tokens, images
        calls.append(
            {
                "message": message,
                "chat_id": chat_id,
                "system_prompt": str(system_prompt or ""),
            }
        )
        idx = len(calls)
        yield f"ответ_{idx}"

    monkeypatch.setattr(
        command_handlers.openclaw_client,
        "send_message_stream",
        fake_send_message_stream,
    )

    bot = _BotStub(args="swarm Тестовая тема")
    message = _MessageStub(text="!agent swarm Тестовая тема")

    await command_handlers.handle_agent(bot, message)

    # Первый reply — статус запуска роя.
    assert any("Запускаю роевой раунд" in text for text in message.reply_calls)
    # Должно быть 3 последовательных вызова (аналитик/критик/интегратор).
    assert len(calls) == 3
    assert all(item["chat_id"] == "swarm:123" for item in calls)

    # Итог публикуется через edit первого status-сообщения.
    all_edits: list[str] = []
    for status in message._status_messages:
        all_edits.extend(status.edits)
    merged = "\n".join(all_edits)
    assert "Swarm Room: Тестовая тема" in merged
    assert "Аналитик" in merged
    assert "Критик" in merged
    assert "Интегратор" in merged
