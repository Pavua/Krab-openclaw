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


# ---------------------------------------------------------------------------
# Structured reflect wiring tests (SWARM_STRUCTURED_REFLECT env toggle)
# ---------------------------------------------------------------------------


@pytest.mark.skip(
    reason="Wave 11: structured kwarg + SWARM_STRUCTURED_REFLECT не реализованы в pipeline; "
    "тест ждёт нового functionality (см. backlog)"
)
@pytest.mark.asyncio
async def test_pipeline_calls_structured_reflect_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pipeline.run(structured=True) → structured_reflect вызван и flush выполнен."""
    import src.core.swarm_self_reflection as reflect_module

    captured: dict[str, object] = {"called": False, "flushed": 0}

    async def mock_structured_reflect(
        task_id: str,
        task_title: str,
        task_description: str,
        task_result: str,
        llm_caller: object,
    ) -> reflect_module.ReflectionOutput:
        captured["called"] = True
        return reflect_module.ReflectionOutput(insights=["insight1"], follow_ups=[])

    def mock_flush(reflection: reflect_module.ReflectionOutput, owner_id: str = "self") -> int:
        captured["flushed"] = 1
        return 1

    monkeypatch.setattr(reflect_module, "structured_reflect", mock_structured_reflect)
    monkeypatch.setattr(reflect_module, "flush_followups_to_reminders", mock_flush)

    # Stub openclaw_client с async generator send_message_stream
    async def _empty_stream(*_a: object, **_kw: object):  # noqa: ANN202
        return
        yield  # делает функцию async generator

    stub_client = type("Client", (), {"send_message_stream": _empty_stream})()

    # Stub AgentRoom.run_round
    import src.core.swarm as swarm_module

    async def mock_run_round(self: object, *_a: object, **_kw: object) -> str:  # noqa: ANN001
        return "research result"

    monkeypatch.setattr(swarm_module.AgentRoom, "run_round", mock_run_round)

    import src.core.swarm_research_pipeline as pipeline_module

    pipeline = pipeline_module.SwarmResearchPipeline()
    await pipeline.run(
        raw_topic="тест structured",
        router_factory=lambda _: object(),
        swarm_bus=object(),
        openclaw_client=stub_client,
        reflect=True,
        structured=True,
    )

    assert captured["called"] is True, "structured_reflect должен быть вызван"
    assert captured["flushed"] == 1, "flush_followups_to_reminders должен быть вызван"


@pytest.mark.skip(
    reason="Wave 11: SWARM_STRUCTURED_REFLECT module attr не реализован в pipeline"
)
@pytest.mark.asyncio
async def test_pipeline_skips_structured_when_env_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SWARM_STRUCTURED_REFLECT=false + structured=None → structured_reflect НЕ вызван."""
    monkeypatch.setenv("SWARM_STRUCTURED_REFLECT", "false")

    import src.core.swarm_self_reflection as reflect_module

    called: dict[str, bool] = {"structured": False}

    async def mock_structured_reflect(**_kw: object) -> reflect_module.ReflectionOutput:
        called["structured"] = True
        return reflect_module.ReflectionOutput()

    monkeypatch.setattr(reflect_module, "structured_reflect", mock_structured_reflect)

    import src.core.swarm as swarm_module

    async def mock_run_round(self: object, *_a: object, **_kw: object) -> str:  # noqa: ANN001
        return "result"

    monkeypatch.setattr(swarm_module.AgentRoom, "run_round", mock_run_round)

    # Переприменяем env-флаг в модуле (он вычитывается при импорте)
    import src.core.swarm_research_pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "SWARM_STRUCTURED_REFLECT", False)

    async def _empty_stream(*_a: object, **_kw: object):  # noqa: ANN202
        return
        yield

    stub_client = type("Client", (), {"send_message_stream": _empty_stream})()

    pipeline = pipeline_module.SwarmResearchPipeline()
    await pipeline.run(
        raw_topic="тест disabled",
        router_factory=lambda _: object(),
        swarm_bus=object(),
        openclaw_client=stub_client,
        reflect=True,
        structured=None,  # использует env-флаг → False
    )

    assert called["structured"] is False, "structured_reflect НЕ должен быть вызван при флаге False"


@pytest.mark.skip(
    reason="Wave 11: structured kwarg в pipeline.run() не реализован"
)
@pytest.mark.asyncio
async def test_pipeline_skips_structured_when_explicit_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """structured=False явно → structured_reflect НЕ вызван, даже если env=true."""
    import src.core.swarm_self_reflection as reflect_module

    called: dict[str, bool] = {"structured": False}

    async def mock_structured_reflect(**_kw: object) -> reflect_module.ReflectionOutput:
        called["structured"] = True
        return reflect_module.ReflectionOutput()

    monkeypatch.setattr(reflect_module, "structured_reflect", mock_structured_reflect)

    import src.core.swarm as swarm_module

    async def mock_run_round(self: object, *_a: object, **_kw: object) -> str:  # noqa: ANN001
        return "result"

    monkeypatch.setattr(swarm_module.AgentRoom, "run_round", mock_run_round)

    import src.core.swarm_research_pipeline as pipeline_module

    async def _empty_stream(*_a: object, **_kw: object):  # noqa: ANN202
        return
        yield

    stub_client = type("Client", (), {"send_message_stream": _empty_stream})()

    pipeline = pipeline_module.SwarmResearchPipeline()
    await pipeline.run(
        raw_topic="тест explicit false",
        router_factory=lambda _: object(),
        swarm_bus=object(),
        openclaw_client=stub_client,
        reflect=True,
        structured=False,
    )

    assert called["structured"] is False, (
        "structured_reflect НЕ должен быть вызван при structured=False"
    )
