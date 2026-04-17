# -*- coding: utf-8 -*-
"""
Тесты проверяют, что `!swarm research` передаёт в `SwarmResearchPipeline.run()`
синглтоны openclaw_client + swarm_task_board и флаг reflect=True (Wave 7-H).

Без этой проводки self-reflection hook внутри pipeline становится no-op
(openclaw_client is None → ранний выход).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.handlers.command_handlers as command_handlers

# ---------------------------------------------------------------------------
# Заглушки — повторяют тот же паттерн, что и test_swarm_research_command.
# ---------------------------------------------------------------------------


class _StatusMessage:
    def __init__(self) -> None:
        self.edits: list[str] = []

    async def edit(self, text: str) -> None:
        self.edits.append(text)


class _MessageStub:
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
    def __init__(self, args: str) -> None:
        self._args = args

    def _get_command_args(self, _message: _MessageStub) -> str:
        return self._args

    def _get_access_profile(self, _user: object) -> SimpleNamespace:
        return SimpleNamespace(level="owner")

    def _is_allowed_sender(self, _user: object) -> bool:
        return True

    def _build_system_prompt_for_sender(self, **_kwargs: object) -> str:
        return "system"


# ---------------------------------------------------------------------------
# Тест: pipeline.run() получает openclaw_client + task_board + reflect=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_swarm_research_wires_reflect_singletons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that handle_swarm('research …') calls pipeline.run() with
    openclaw_client + task_board + reflect=True."""
    captured: dict[str, object] = {}

    class _MockPipeline:
        async def run(self, raw_topic: str, **kwargs: object) -> str:
            captured["raw_topic"] = raw_topic
            captured.update(kwargs)
            return "stub result"

    # Подменяем factory в модуле: SwarmResearchPipeline() в command_handlers
    # импортируется lazy внутри функции → патчим путь импорта.
    import src.core.swarm_research_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SwarmResearchPipeline", _MockPipeline)

    # Чтобы не разбивать telegram-сообщение на чанки
    monkeypatch.setattr(command_handlers, "_split_text_for_telegram", lambda t: [t])

    bot = _BotStub(args="research тренды AI 2025")
    message = _MessageStub(text="!swarm research тренды AI 2025")

    await command_handlers.handle_swarm(bot, message)

    # Pipeline.run() был вызван с темой
    assert captured.get("raw_topic") == "тренды AI 2025"

    # Wiring: reflect singletons переданы не-None
    assert "openclaw_client" in captured, "openclaw_client не передан в pipeline.run()"
    assert captured["openclaw_client"] is not None, "openclaw_client должен быть синглтоном"

    assert "task_board" in captured, "task_board не передан в pipeline.run()"
    assert captured["task_board"] is not None, "task_board должен быть синглтоном"

    # Явный flag reflect=True
    assert captured.get("reflect") is True, "reflect должен быть True по умолчанию"


@pytest.mark.asyncio
async def test_swarm_research_passes_real_openclaw_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Переданный openclaw_client — это именно module-level синглтон."""
    from src.openclaw_client import openclaw_client as real_client

    captured: dict[str, object] = {}

    class _MockPipeline:
        async def run(self, raw_topic: str, **kwargs: object) -> str:
            captured.update(kwargs)
            return "ok"

    import src.core.swarm_research_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SwarmResearchPipeline", _MockPipeline)
    monkeypatch.setattr(command_handlers, "_split_text_for_telegram", lambda t: [t])

    bot = _BotStub(args="research test topic")
    message = _MessageStub(text="!swarm research test topic")

    await command_handlers.handle_swarm(bot, message)

    assert captured.get("openclaw_client") is real_client


@pytest.mark.asyncio
async def test_swarm_research_passes_real_task_board_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Переданный task_board — это именно singleton swarm_task_board."""
    from src.core.swarm_task_board import swarm_task_board as real_board

    captured: dict[str, object] = {}

    class _MockPipeline:
        async def run(self, raw_topic: str, **kwargs: object) -> str:
            captured.update(kwargs)
            return "ok"

    import src.core.swarm_research_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SwarmResearchPipeline", _MockPipeline)
    monkeypatch.setattr(command_handlers, "_split_text_for_telegram", lambda t: [t])

    bot = _BotStub(args="research test topic")
    message = _MessageStub(text="!swarm research test topic")

    await command_handlers.handle_swarm(bot, message)

    assert captured.get("task_board") is real_board
