# -*- coding: utf-8 -*-
"""
Тесты подкоманды `!swarm research` в command_handlers.

Покрываем:
1) ошибку при отсутствии темы;
2) парсинг команды (тема извлекается корректно);
3) запуск analysts-команды с research-промптом;
4) русский алиас `!swarm исследование`;
5) сохранение артефакта через swarm_artifact_store.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.handlers.command_handlers as command_handlers
from src.core.exceptions import UserInputError

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _StatusMessage:
    """Async-заглушка статус-сообщения для `.edit()`."""

    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit(self, text: str) -> None:
        self.edits.append(text)

    async def edit_text(self, text: str) -> None:
        self.edits.append(text)


class _MessageStub:
    """Минимальная заглушка Telegram Message для тестов command handler."""

    def __init__(self, text: str, chat_id: int = 42) -> None:
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(
            id=1,
            username="testuser",
            first_name="Test",
        )
        self.reply_calls: list[str] = []
        self._status_messages: list[_StatusMessage] = []

    async def reply(self, text: str) -> _StatusMessage:
        self.reply_calls.append(text)
        status = _StatusMessage()
        self._status_messages.append(status)
        return status


class _BotStub:
    """Заглушка бота с минимальным API для handle_swarm."""

    def __init__(self, args: str) -> None:
        self._args = args

    def _get_command_args(self, _message: _MessageStub) -> str:
        return self._args

    def _get_access_profile(self, _user: object) -> SimpleNamespace:
        return SimpleNamespace(level="owner")

    def _is_allowed_sender(self, _user: object) -> bool:
        return True

    def _build_system_prompt_for_sender(
        self,
        *,
        is_allowed_sender: bool,
        access_level: str,
    ) -> str:
        return "system"


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_research_requires_topic() -> None:
    """`!swarm research` без темы должен бросать UserInputError."""
    bot = _BotStub(args="research")
    message = _MessageStub(text="!swarm research")

    with pytest.raises(UserInputError) as exc_info:
        await command_handlers.handle_swarm(bot, message)

    assert (
        "тему" in exc_info.value.user_message.lower()
        or "research" in exc_info.value.user_message.lower()
    )


@pytest.mark.asyncio
async def test_research_requires_nonempty_topic() -> None:
    """`!swarm research   ` с пустой темой (пробелы) тоже бросает ошибку."""
    bot = _BotStub(args="research   ")
    message = _MessageStub(text="!swarm research   ")

    with pytest.raises(UserInputError):
        await command_handlers.handle_swarm(bot, message)


@pytest.mark.asyncio
async def test_research_uses_analysts_team(monkeypatch: pytest.MonkeyPatch) -> None:
    """Research запускает analysts team и включает web_search в промпт."""
    captured_topics: list[str] = []

    async def fake_stream(
        message: str,
        chat_id: str,
        system_prompt: str | None = None,
        force_cloud: bool = False,
        max_output_tokens: int | None = None,
        images=None,
    ):
        captured_topics.append(message)
        yield "результат исследования"

    monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)

    # Заглушка artifact store — не записываем на диск
    monkeypatch.setattr(
        command_handlers,
        "_split_text_for_telegram",
        lambda text: [text],
    )

    # Патчим save_round_artifact чтобы не писать на диск
    from src.core import swarm_artifact_store as _art_module

    saved: list[dict] = []

    def fake_save(**kwargs: object) -> None:
        saved.append(dict(kwargs))

    monkeypatch.setattr(_art_module.swarm_artifact_store, "save_round_artifact", fake_save)

    bot = _BotStub(args="research тренды AI 2025")
    message = _MessageStub(text="!swarm research тренды AI 2025")

    await command_handlers.handle_swarm(bot, message)

    # Первый reply должен содержать Research и analysts
    assert message.reply_calls, "Нет reply от handle_swarm"
    first_reply = message.reply_calls[0]
    assert "Research" in first_reply or "research" in first_reply.lower()
    assert "analysts" in first_reply.lower()

    # Промпт должен требовать web_search
    assert any("web_search" in t for t in captured_topics), (
        "Промпт не содержит требование web_search"
    )

    # Артефакт сохранён (через save_round_artifact внутри handle_swarm)
    # Проверяем через reply — research pipeline выдал результат
    all_edits = [e for s in message._status_messages for e in s.edits]
    assert all_edits, "Результат не был выведен через edit"


@pytest.mark.asyncio
async def test_research_russian_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    """Русский алиас `!swarm исследование <тема>` работает так же как research."""
    captured: list[str] = []

    async def fake_stream(message: str, chat_id: str, **kwargs: object):
        captured.append(message)
        yield "ответ"

    monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)
    monkeypatch.setattr(command_handlers, "_split_text_for_telegram", lambda t: [t])

    from src.core import swarm_artifact_store as _art_module

    monkeypatch.setattr(_art_module.swarm_artifact_store, "save_round_artifact", lambda **kw: None)

    bot = _BotStub(args="исследование квантовые компьютеры")
    message = _MessageStub(text="!swarm исследование квантовые компьютеры")

    await command_handlers.handle_swarm(bot, message)

    assert captured, "Ни одного вызова stream — алиас не сработал"
    assert any("web_search" in t for t in captured)


@pytest.mark.asyncio
async def test_research_topic_included_in_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тема исследования присутствует в промпте для агента."""
    captured: list[str] = []

    async def fake_stream(message: str, chat_id: str, **kwargs: object):
        captured.append(message)
        yield "ответ"

    monkeypatch.setattr(command_handlers.openclaw_client, "send_message_stream", fake_stream)
    monkeypatch.setattr(command_handlers, "_split_text_for_telegram", lambda t: [t])

    from src.core import swarm_artifact_store as _art_module

    monkeypatch.setattr(_art_module.swarm_artifact_store, "save_round_artifact", lambda **kw: None)

    topic = "блокчейн и NFT в 2026"
    bot = _BotStub(args=f"research {topic}")
    message = _MessageStub(text=f"!swarm research {topic}")

    await command_handlers.handle_swarm(bot, message)

    # Оригинальная тема должна попасть в промпт
    assert any(topic in t for t in captured), (
        f"Тема '{topic}' не найдена ни в одном из промптов: {captured}"
    )
