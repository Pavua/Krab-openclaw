# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm_bus.py.

Покрывает:
- TEAM_REGISTRY — структура и содержимое
- resolve_team_name — прямые имена, псевдонимы, unknown, регистр
- list_teams — форматирование, наличие всех команд
- SwarmBus.dispatch — нормальный путь, max_depth, неизвестная команда, ошибка AgentRoom
- SwarmBus.active_count — учёт активных задач
- SwarmBusTask — инициализация, uuid, done_event
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_bus import (
    TEAM_REGISTRY,
    SwarmBus,
    SwarmBusTask,
    list_teams,
    resolve_team_name,
    swarm_bus,
)

# ---------------------------------------------------------------------------
# TEAM_REGISTRY — структура
# ---------------------------------------------------------------------------


class TestTeamRegistry:
    """Структура реестра команд."""

    def test_all_expected_teams_present(self):
        """Все четыре команды определены."""
        assert "traders" in TEAM_REGISTRY
        assert "coders" in TEAM_REGISTRY
        assert "analysts" in TEAM_REGISTRY
        assert "creative" in TEAM_REGISTRY

    def test_each_team_has_three_roles(self):
        """Каждая команда содержит ровно три роли."""
        for team_name, roles in TEAM_REGISTRY.items():
            assert len(roles) == 3, (
                f"Команда {team_name!r} содержит {len(roles)} ролей, ожидалось 3"
            )

    def test_roles_have_required_fields(self):
        """Каждая роль содержит обязательные поля."""
        required = {"name", "emoji", "title", "system_hint"}
        for team_name, roles in TEAM_REGISTRY.items():
            for role in roles:
                missing = required - role.keys()
                assert not missing, (
                    f"Команда {team_name!r}, роль {role.get('name')!r} не имеет полей: {missing}"
                )

    def test_traders_has_delegate_hint(self):
        """Роли traders содержат [DELEGATE: coders] в system_hint (для тестирования делегирования)."""
        traders_roles = TEAM_REGISTRY["traders"]
        # риск-менеджер или трейдер должен иметь делегирование к coders
        hints = " ".join(r["system_hint"] for r in traders_roles)
        assert "[DELEGATE: coders]" in hints

    def test_team_registry_is_copy_safe(self):
        """TEAM_REGISTRY — это dict; мутация списка команд не ломает основной реестр."""
        original_len = len(TEAM_REGISTRY["coders"])
        copy = list(TEAM_REGISTRY["coders"])
        copy.append({"name": "extra", "emoji": "x", "title": "X", "system_hint": "x"})
        assert len(TEAM_REGISTRY["coders"]) == original_len


# ---------------------------------------------------------------------------
# resolve_team_name
# ---------------------------------------------------------------------------


class TestResolveTeamName:
    """Резолв имён команд."""

    def test_direct_name_returned(self):
        assert resolve_team_name("traders") == "traders"
        assert resolve_team_name("coders") == "coders"
        assert resolve_team_name("analysts") == "analysts"
        assert resolve_team_name("creative") == "creative"

    def test_alias_ru_traders(self):
        assert resolve_team_name("трейдеры") == "traders"
        assert resolve_team_name("торговля") == "traders"
        assert resolve_team_name("крипта") == "traders"

    def test_alias_ru_coders(self):
        assert resolve_team_name("кодеры") == "coders"
        assert resolve_team_name("разработка") == "coders"
        assert resolve_team_name("код") == "coders"

    def test_alias_dev(self):
        assert resolve_team_name("dev") == "coders"

    def test_alias_ru_analysts(self):
        assert resolve_team_name("аналитика") == "analysts"
        assert resolve_team_name("анализ") == "analysts"

    def test_alias_ru_creative(self):
        assert resolve_team_name("креатив") == "creative"
        assert resolve_team_name("идеи") == "creative"

    def test_unknown_returns_none(self):
        assert resolve_team_name("unknown_team") is None
        assert resolve_team_name("") is None
        assert resolve_team_name("xyz") is None

    def test_strips_whitespace(self):
        """Пробелы вокруг имени обрезаются."""
        assert resolve_team_name("  traders  ") == "traders"
        assert resolve_team_name("  кодеры  ") == "coders"

    def test_case_insensitive(self):
        """Регистр не важен."""
        assert resolve_team_name("TRADERS") == "traders"
        assert resolve_team_name("Coders") == "coders"


# ---------------------------------------------------------------------------
# list_teams
# ---------------------------------------------------------------------------


class TestListTeams:
    """Форматированный список команд."""

    def test_returns_string(self):
        result = list_teams()
        assert isinstance(result, str)

    def test_contains_all_teams(self):
        result = list_teams()
        assert "traders" in result
        assert "coders" in result
        assert "analysts" in result
        assert "creative" in result

    def test_contains_usage_hint(self):
        result = list_teams()
        assert "!swarm" in result

    def test_contains_aliases_note(self):
        """Упоминаются псевдонимы для удобства пользователя."""
        result = list_teams()
        assert "трейдеры" in result or "Псевдонимы" in result


# ---------------------------------------------------------------------------
# SwarmBusTask
# ---------------------------------------------------------------------------


class TestSwarmBusTask:
    """Dataclass для задачи шины."""

    def test_default_task_id_is_short(self):
        task = SwarmBusTask()
        assert len(task.task_id) == 8

    def test_done_event_created(self):
        task = SwarmBusTask()
        assert isinstance(task.done_event, asyncio.Event)
        assert not task.done_event.is_set()

    def test_result_and_error_none_by_default(self):
        task = SwarmBusTask()
        assert task.result is None
        assert task.error is None

    def test_unique_ids(self):
        """Каждая задача получает уникальный ID."""
        ids = {SwarmBusTask().task_id for _ in range(20)}
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# SwarmBus.dispatch
# ---------------------------------------------------------------------------


class TestSwarmBusDispatch:
    """Диспетчер задач SwarmBus."""

    def _make_bus(self) -> SwarmBus:
        return SwarmBus()

    @pytest.mark.asyncio
    async def test_dispatch_max_depth_blocks(self):
        """Превышение max_depth возвращает сообщение об отказе, не создаёт AgentRoom."""
        bus = self._make_bus()
        result = await bus.dispatch(
            source_team="traders",
            target_team="coders",
            topic="напиши бота",
            router_factory=MagicMock(),
            depth=SwarmBus._MAX_DEPTH,
        )
        assert "Делегирование отклонено" in result
        assert "глубина" in result.lower() or str(SwarmBus._MAX_DEPTH) in result

    @pytest.mark.asyncio
    async def test_dispatch_unknown_team_returns_error(self):
        """Несуществующая команда возвращает список доступных команд."""
        bus = self._make_bus()
        result = await bus.dispatch(
            source_team="traders",
            target_team="ghosts",
            topic="задача",
            router_factory=MagicMock(),
            depth=0,
        )
        assert "ghosts" in result
        assert "не найдена" in result

    @pytest.mark.asyncio
    async def test_dispatch_alias_resolved(self):
        """Псевдоним команды корректно резолвится."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(return_value="Код написан")
        mock_router = MagicMock()
        mock_router_factory = MagicMock(return_value=mock_router)

        # AgentRoom импортируется локально внутри dispatch() из src.core.swarm
        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            result = await bus.dispatch(
                source_team="traders",
                target_team="кодеры",  # псевдоним
                topic="бот",
                router_factory=mock_router_factory,
                depth=0,
            )
        assert result == "Код написан"

    @pytest.mark.asyncio
    async def test_dispatch_normal_flow(self):
        """Нормальный путь: AgentRoom.run_round вызывается с правильными параметрами."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(return_value="Результат coders")
        mock_router = MagicMock()
        mock_router_factory = MagicMock(return_value=mock_router)

        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            result = await bus.dispatch(
                source_team="traders",
                target_team="coders",
                topic="создай бота",
                router_factory=mock_router_factory,
                depth=0,
            )

        assert result == "Результат coders"
        mock_room.run_round.assert_called_once()
        call_kwargs = mock_room.run_round.call_args
        assert call_kwargs[0][0] == "создай бота"  # topic — первый позиционный аргумент

    @pytest.mark.asyncio
    async def test_dispatch_exception_returns_error_string(self):
        """Исключение из AgentRoom.run_round возвращает строку с описанием ошибки."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(side_effect=RuntimeError("LLM failure"))
        mock_router_factory = MagicMock(return_value=MagicMock())

        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            result = await bus.dispatch(
                source_team="analysts",
                target_team="coders",
                topic="задача",
                router_factory=mock_router_factory,
                depth=0,
            )

        assert "Ошибка выполнения команды" in result
        assert "coders" in result

    @pytest.mark.asyncio
    async def test_dispatch_task_cleaned_up_on_success(self):
        """После успешного dispatch задача удаляется из _active_tasks."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(return_value="ОК")
        mock_router_factory = MagicMock(return_value=MagicMock())

        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            await bus.dispatch(
                source_team="analysts",
                target_team="coders",
                topic="задача",
                router_factory=mock_router_factory,
                depth=0,
            )

        assert bus.active_count() == 0

    @pytest.mark.asyncio
    async def test_dispatch_task_cleaned_up_on_error(self):
        """После ошибки dispatch задача тоже удаляется (finally-блок)."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(side_effect=Exception("crash"))
        mock_router_factory = MagicMock(return_value=MagicMock())

        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            await bus.dispatch(
                source_team="traders",
                target_team="coders",
                topic="задача",
                router_factory=mock_router_factory,
                depth=0,
            )

        assert bus.active_count() == 0

    @pytest.mark.asyncio
    async def test_dispatch_depth_incremented(self):
        """AgentRoom вызывается с _depth = depth + 1."""
        bus = self._make_bus()

        mock_room = MagicMock()
        mock_room.run_round = AsyncMock(return_value="ОК")
        mock_router = MagicMock()
        mock_router_factory = MagicMock(return_value=mock_router)

        with patch("src.core.swarm.AgentRoom", return_value=mock_room):
            await bus.dispatch(
                source_team="traders",
                target_team="coders",
                topic="задача",
                router_factory=mock_router_factory,
                depth=0,
            )

        call_kwargs = mock_room.run_round.call_args.kwargs
        assert call_kwargs.get("_depth") == 1  # 0 + 1


# ---------------------------------------------------------------------------
# Singleton swarm_bus
# ---------------------------------------------------------------------------


class TestSwarmBusSingleton:
    """Модульный singleton."""

    def test_singleton_is_swarm_bus_instance(self):
        assert isinstance(swarm_bus, SwarmBus)

    def test_singleton_active_count_starts_zero(self):
        assert swarm_bus.active_count() == 0
