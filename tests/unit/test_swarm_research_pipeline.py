# -*- coding: utf-8 -*-
"""
Тесты для SwarmResearchPipeline (src/core/swarm_research_pipeline.py).

Покрываем:
- ResearchConfig дефолты и кастомизацию
- build_prompt: structured и free форматы
- run(): успешный путь, ошибки AgentRoom, сохранение артефакта
- синглтон get_research_pipeline()
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_research_pipeline import (
    ResearchConfig,
    SwarmResearchPipeline,
    get_research_pipeline,
)


# ---------------------------------------------------------------------------
# ResearchConfig
# ---------------------------------------------------------------------------


class TestResearchConfig:
    def test_defaults(self) -> None:
        cfg = ResearchConfig()
        assert cfg.max_sources == 5
        assert cfg.max_rounds == 1
        assert cfg.output_format == "structured"
        assert cfg.team_key == "analysts"

    def test_custom_values(self) -> None:
        cfg = ResearchConfig(max_sources=10, max_rounds=3, output_format="free", team_key="coders")
        assert cfg.max_sources == 10
        assert cfg.max_rounds == 3
        assert cfg.output_format == "free"
        assert cfg.team_key == "coders"

    def test_dataclass_equality(self) -> None:
        assert ResearchConfig() == ResearchConfig()
        assert ResearchConfig(max_sources=3) != ResearchConfig(max_sources=7)


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_structured_contains_web_search(self) -> None:
        pipeline = SwarmResearchPipeline()
        prompt = pipeline.build_prompt("тренды AI 2025")
        assert "web_search" in prompt
        assert "тренды AI 2025" in prompt

    def test_structured_contains_sections(self) -> None:
        pipeline = SwarmResearchPipeline()
        prompt = pipeline.build_prompt("блокчейн")
        assert "Summary" in prompt
        assert "Key Findings" in prompt
        assert "Sources" in prompt

    def test_structured_max_sources_in_prompt(self) -> None:
        cfg = ResearchConfig(max_sources=8)
        pipeline = SwarmResearchPipeline(config=cfg)
        prompt = pipeline.build_prompt("квантовые компьютеры")
        assert "8" in prompt

    def test_free_format(self) -> None:
        cfg = ResearchConfig(output_format="free")
        pipeline = SwarmResearchPipeline(config=cfg)
        prompt = pipeline.build_prompt("криптовалюты")
        assert "web_search" in prompt
        # В free-формате нет структурированных разделов
        assert "Summary" not in prompt

    def test_free_format_sources_count(self) -> None:
        cfg = ResearchConfig(output_format="free", max_sources=3)
        pipeline = SwarmResearchPipeline(config=cfg)
        prompt = pipeline.build_prompt("тема")
        assert "3" in prompt

    def test_prompt_starts_with_topic(self) -> None:
        pipeline = SwarmResearchPipeline()
        prompt = pipeline.build_prompt("моя тема")
        assert "моя тема" in prompt

    def test_empty_topic(self) -> None:
        """Пустая тема не ломает build_prompt."""
        pipeline = SwarmResearchPipeline()
        prompt = pipeline.build_prompt("")
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ---------------------------------------------------------------------------
# run() — успешный путь
# ---------------------------------------------------------------------------


class TestSwarmResearchPipelineRun:
    @pytest.fixture()
    def mock_env(self):
        """Патчит все внешние зависимости pipeline через модули-источники."""
        fake_result = "Summary: тест\nKey Findings: данные\nSources: src1, src2"

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(return_value=fake_result)

        mock_artifact_store = MagicMock()
        mock_artifact_store.save_round_artifact = MagicMock()

        team_registry = {"analysts": ["analyst_role"]}

        # AgentRoom импортируется внутри run() через 'from .swarm import AgentRoom',
        # поэтому патчим в модуле-источнике src.core.swarm
        with (
            patch("src.core.swarm_research_pipeline.AgentRoom", return_value=mock_room) as p_room,
            patch(
                "src.core.swarm_research_pipeline.swarm_artifact_store",
                mock_artifact_store,
            ),
            patch("src.core.swarm_research_pipeline.TEAM_REGISTRY", team_registry),
        ):
            yield {
                "mock_room": mock_room,
                "mock_artifact_store": mock_artifact_store,
                "fake_result": fake_result,
                "p_room": p_room,
            }

    @pytest.mark.asyncio
    async def test_run_returns_result(self, mock_env) -> None:
        pipeline = SwarmResearchPipeline()
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        result = await pipeline.run(
            "тренды AI",
            router_factory=router_factory,
            swarm_bus=swarm_bus,
        )
        assert result == mock_env["fake_result"]

    @pytest.mark.asyncio
    async def test_run_calls_room_run_round(self, mock_env) -> None:
        pipeline = SwarmResearchPipeline()
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        await pipeline.run("тема", router_factory=router_factory, swarm_bus=swarm_bus)

        mock_env["mock_room"].run_round.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_saves_artifact(self, mock_env) -> None:
        pipeline = SwarmResearchPipeline()
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        await pipeline.run("моя тема", router_factory=router_factory, swarm_bus=swarm_bus)

        mock_env["mock_artifact_store"].save_round_artifact.assert_called_once()
        call_kwargs = mock_env["mock_artifact_store"].save_round_artifact.call_args
        assert "[research] моя тема" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_run_uses_correct_team(self, mock_env) -> None:
        """Правильная команда передаётся в router_factory."""
        cfg = ResearchConfig(team_key="coders")
        pipeline = SwarmResearchPipeline(config=cfg)
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        # Патчим TEAM_REGISTRY с командой coders
        with patch("src.core.swarm_research_pipeline.TEAM_REGISTRY", {"coders": ["coder_role"]}):
            await pipeline.run("тема", router_factory=router_factory, swarm_bus=swarm_bus)

        router_factory.assert_called_with("coders")

    @pytest.mark.asyncio
    async def test_run_passes_swarm_bus(self, mock_env) -> None:
        pipeline = SwarmResearchPipeline()
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        await pipeline.run("тема", router_factory=router_factory, swarm_bus=swarm_bus)

        call_kwargs = mock_env["mock_room"].run_round.call_args
        assert call_kwargs.kwargs.get("_bus") is swarm_bus

    @pytest.mark.asyncio
    async def test_run_artifact_team_matches_config(self, mock_env) -> None:
        cfg = ResearchConfig(team_key="analysts")
        pipeline = SwarmResearchPipeline(config=cfg)
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        await pipeline.run("тест", router_factory=router_factory, swarm_bus=swarm_bus)

        save_call = mock_env["mock_artifact_store"].save_round_artifact.call_args
        assert save_call.kwargs.get("team") == "analysts"

    @pytest.mark.asyncio
    async def test_run_result_stored_in_artifact(self, mock_env) -> None:
        pipeline = SwarmResearchPipeline()
        router_factory = MagicMock(return_value=MagicMock())
        swarm_bus = MagicMock()

        await pipeline.run("тест", router_factory=router_factory, swarm_bus=swarm_bus)

        save_call = mock_env["mock_artifact_store"].save_round_artifact.call_args
        assert save_call.kwargs.get("result") == mock_env["fake_result"]


# ---------------------------------------------------------------------------
# run() — обработка ошибок
# ---------------------------------------------------------------------------


class TestSwarmResearchPipelineErrors:
    @pytest.mark.asyncio
    async def test_run_propagates_room_error(self) -> None:
        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(side_effect=RuntimeError("room failure"))

        with (
            patch("src.core.swarm_research_pipeline.AgentRoom", return_value=mock_room),
            patch("src.core.swarm_research_pipeline.TEAM_REGISTRY", {"analysts": []}),
            patch("src.core.swarm_research_pipeline.swarm_artifact_store", MagicMock()),
        ):
            pipeline = SwarmResearchPipeline()
            with pytest.raises(RuntimeError, match="room failure"):
                await pipeline.run(
                    "тема",
                    router_factory=MagicMock(return_value=MagicMock()),
                    swarm_bus=MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_run_artifact_not_saved_on_error(self) -> None:
        """Артефакт НЕ сохраняется при ошибке AgentRoom."""
        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(side_effect=ValueError("oops"))
        mock_store = MagicMock()

        with (
            patch("src.core.swarm_research_pipeline.AgentRoom", return_value=mock_room),
            patch("src.core.swarm_research_pipeline.TEAM_REGISTRY", {"analysts": []}),
            patch("src.core.swarm_research_pipeline.swarm_artifact_store", mock_store),
        ):
            pipeline = SwarmResearchPipeline()
            with pytest.raises(ValueError):
                await pipeline.run(
                    "тема",
                    router_factory=MagicMock(return_value=MagicMock()),
                    swarm_bus=MagicMock(),
                )

        mock_store.save_round_artifact.assert_not_called()


# ---------------------------------------------------------------------------
# Конфигурируемые параметры
# ---------------------------------------------------------------------------


class TestResearchConfigIntegration:
    @pytest.mark.asyncio
    async def test_custom_output_format_in_prompt(self) -> None:
        """free-формат не содержит Summary/Key Findings."""
        cfg = ResearchConfig(output_format="free", max_sources=4)
        pipeline = SwarmResearchPipeline(config=cfg)
        prompt = pipeline.build_prompt("тест")
        assert "Summary" not in prompt
        assert "Key Findings" not in prompt
        assert "4" in prompt

    def test_max_rounds_stored(self) -> None:
        cfg = ResearchConfig(max_rounds=5)
        pipeline = SwarmResearchPipeline(config=cfg)
        assert pipeline.config.max_rounds == 5

    def test_none_config_uses_defaults(self) -> None:
        pipeline = SwarmResearchPipeline(config=None)
        assert pipeline.config == ResearchConfig()


# ---------------------------------------------------------------------------
# Синглтон get_research_pipeline()
# ---------------------------------------------------------------------------


class TestGetResearchPipeline:
    def setup_method(self) -> None:
        """Сбрасываем синглтон перед каждым тестом."""
        import src.core.swarm_research_pipeline as mod
        mod._default_pipeline = None

    def test_returns_pipeline_instance(self) -> None:
        p = get_research_pipeline()
        assert isinstance(p, SwarmResearchPipeline)

    def test_singleton_same_object(self) -> None:
        p1 = get_research_pipeline()
        p2 = get_research_pipeline()
        assert p1 is p2

    def test_custom_config_creates_new_instance(self) -> None:
        p1 = get_research_pipeline()
        cfg = ResearchConfig(max_sources=99)
        p2 = get_research_pipeline(config=cfg)
        assert p2 is not p1
        assert p2.config.max_sources == 99

    def test_singleton_after_custom_config(self) -> None:
        """После кастомной конфигурации синглтон обновляется."""
        cfg = ResearchConfig(max_sources=7)
        get_research_pipeline(config=cfg)
        p = get_research_pipeline()
        # Синглтон теперь — последний созданный
        assert p.config.max_sources == 7
