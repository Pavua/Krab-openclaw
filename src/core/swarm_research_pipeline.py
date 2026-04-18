# -*- coding: utf-8 -*-
"""
Research Pipeline для Swarm — выделенный модуль (Phase 7).

Инкапсулирует всю бизнес-логику `!swarm research`:
- формирование промпта с обязательным web_search
- запуск AgentRoom через router
- сохранение артефакта в swarm_artifact_store
- конфигурируемые параметры: max_sources, max_rounds, output_format
"""

from __future__ import annotations

import dataclasses
import os
import time
from typing import TYPE_CHECKING, Callable

from .logger import get_logger
from .swarm import AgentRoom
from .swarm_artifact_store import swarm_artifact_store
from .swarm_bus import TEAM_REGISTRY

if TYPE_CHECKING:
    pass

# Включает structured reflection path (Haiku-style, schema-validated).
# Можно отключить через env: SWARM_STRUCTURED_REFLECT=false
SWARM_STRUCTURED_REFLECT: bool = os.environ.get(
    "SWARM_STRUCTURED_REFLECT", "true"
).lower() in ("true", "1", "yes")

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Конфигурация пайплайна
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ResearchConfig:
    """Параметры research pipeline."""

    # Максимальное кол-во источников, которые просим найти
    max_sources: int = 5
    # Кол-во раундов AgentRoom (пока pipeline делает один раунд, резерв для future)
    max_rounds: int = 1
    # Формат вывода: 'structured' (Summary/Key Findings/Sources) или 'free'
    output_format: str = "structured"
    # Команда свёрма, которая выполняет research
    team_key: str = "analysts"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class SwarmResearchPipeline:
    """
    Research Pipeline для Swarm.

    Пример использования::

        pipeline = SwarmResearchPipeline(config=ResearchConfig(max_sources=7))
        result = await pipeline.run(
            raw_topic="тренды AI 2025",
            router_factory=my_router_factory,
            swarm_bus=swarm_bus,
        )
    """

    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()

    # ------------------------------------------------------------------
    # Формирование промпта
    # ------------------------------------------------------------------

    def build_prompt(self, raw_topic: str) -> str:
        """Собирает промпт с требованиями к web_search и структуре ответа."""
        if self.config.output_format == "structured":
            structure_hint = (
                "Структурируй результат: Summary, Key Findings, Sources "
                f"(не менее {self.config.max_sources} источников)."
            )
        else:
            structure_hint = f"Найди не менее {self.config.max_sources} источников по теме."

        return (
            f"Проведи исследование по теме: {raw_topic}. "
            "Обязательно используй web_search для поиска актуальной информации. " + structure_hint
        )

    # ------------------------------------------------------------------
    # Основной метод
    # ------------------------------------------------------------------

    async def run(
        self,
        raw_topic: str,
        *,
        router_factory: Callable[[str], object],
        swarm_bus: object,
        openclaw_client: object | None = None,
        task_board: object | None = None,
        reflect: bool = True,
        structured: bool | None = None,
    ) -> str:
        """
        Запускает research pipeline.

        Args:
            raw_topic: Тема исследования (без префикса 'research').
            router_factory: Фабрика роутеров `(team_name) -> RouterAdapter`.
            swarm_bus: SwarmBus для межкомандного broadcast.
            openclaw_client: Опциональный клиент для self-reflection LLM-вызова.
            task_board: Опциональный SwarmTaskBoard для follow-up задач.
            reflect: Включает self-reflection hook (Proactivity Level 3).
            structured: Включает structured reflection path (schema-validated).
                None = использовать SWARM_STRUCTURED_REFLECT env-флаг.

        Returns:
            Финальный текст исследования.
        """
        # Определяем флаг structured (env → default, явный аргумент — override)
        use_structured = SWARM_STRUCTURED_REFLECT if structured is None else structured
        team_key = self.config.team_key
        research_prompt = self.build_prompt(raw_topic)

        logger.info(
            "research_pipeline_start",
            topic=raw_topic,
            team=team_key,
            max_sources=self.config.max_sources,
            output_format=self.config.output_format,
        )

        roles = TEAM_REGISTRY.get(team_key)
        room = AgentRoom(roles=roles)
        router = router_factory(team_key)

        result_text = await room.run_round(
            research_prompt,
            router,
            _bus=swarm_bus,
            _router_factory=router_factory,
            _team_name=team_key,
        )

        # Сохраняем артефакт с меткой [research]
        swarm_artifact_store.save_round_artifact(
            team=team_key,
            topic=f"[research] {raw_topic}",
            result=result_text,
        )

        logger.info(
            "research_pipeline_done",
            topic=raw_topic,
            result_len=len(result_text),
        )

        # Self-Reflection hook (Proactivity Level 3, Session 11)
        if reflect and openclaw_client is not None:
            task_id = f"research:{team_key}:{int(time.time())}"

            # Legacy path
            try:
                from .swarm_self_reflection import enqueue_followups, reflect_on_task

                reflection = await reflect_on_task(
                    task_id=task_id,
                    task_title=f"Research: {raw_topic}",
                    task_description=research_prompt,
                    task_result=result_text,
                    task_status="completed",
                    openclaw_client=openclaw_client,
                )
                if reflection.followups:
                    enqueue_followups(reflection, task_board=task_board)
                    logger.info(
                        "research_self_reflection_followups_enqueued",
                        count=len(reflection.followups),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "research_self_reflection_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

            # Structured path (schema-validated, light model)
            if use_structured:
                try:
                    from .swarm_self_reflection import (
                        flush_followups_to_reminders,
                        structured_reflect,
                    )

                    async def _llm_caller(prompt: str) -> str:
                        """Обёртка над openclaw_client для structured reflection."""
                        chunks: list[str] = []
                        try:
                            async for piece in openclaw_client.send_message_stream(
                                message=prompt,
                                chat_id="__reflection__",
                                force_cloud=True,
                                disable_tools=True,
                            ):
                                if isinstance(piece, str):
                                    chunks.append(piece)
                        except Exception as _exc:  # noqa: BLE001
                            logger.warning(
                                "structured_reflect_stream_failed",
                                error=str(_exc),
                            )
                        return "".join(chunks)

                    structured_result = await structured_reflect(
                        task_id=task_id,
                        task_title=f"Research: {raw_topic}",
                        task_description=research_prompt,
                        task_result=result_text,
                        llm_caller=_llm_caller,
                    )

                    flushed = flush_followups_to_reminders(structured_result)
                    logger.info(
                        "structured_reflect_completed",
                        task_id=task_id,
                        insights=len(structured_result.insights),
                        follow_ups_total=len(structured_result.follow_ups),
                        flushed_to_reminders=flushed,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "structured_reflect_failed",
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

        return result_text


# ---------------------------------------------------------------------------
# Синглтон (удобство для обычного использования)
# ---------------------------------------------------------------------------

_default_pipeline: SwarmResearchPipeline | None = None


def get_research_pipeline(config: ResearchConfig | None = None) -> SwarmResearchPipeline:
    """Возвращает пайплайн с дефолтной конфигурацией (синглтон)."""
    global _default_pipeline
    if _default_pipeline is None or config is not None:
        _default_pipeline = SwarmResearchPipeline(config=config)
    return _default_pipeline
