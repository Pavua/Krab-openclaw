# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm.py и src/core/swarm_bus.py —
роевой оркестратор и шина делегирования.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm import (
    _DELEGATE_PATTERN,
    DEFAULT_AGENT_ROLES,
    AgentRoom,
    SwarmOrchestrator,
    SwarmTask,
)
from src.core.swarm_bus import (
    TEAM_ALIASES,
    TEAM_REGISTRY,
    SwarmBus,
    SwarmBusTask,
    list_teams,
    resolve_team_name,
)

# ---------------------------------------------------------------------------
# TEAM_REGISTRY — структурная валидация
# ---------------------------------------------------------------------------

class TestTeamRegistry:
    """Валидация структуры реестра команд."""

    EXPECTED_TEAMS = {"traders", "coders", "analysts", "creative"}

    def test_all_teams_present(self):
        assert set(TEAM_REGISTRY.keys()) == self.EXPECTED_TEAMS

    def test_every_team_has_roles(self):
        for team, roles in TEAM_REGISTRY.items():
            assert len(roles) >= 2, f"Команда {team} должна иметь >= 2 ролей"

    def test_role_required_fields(self):
        """Каждая роль должна содержать обязательные поля."""
        required = {"name", "emoji", "title", "system_hint"}
        for team, roles in TEAM_REGISTRY.items():
            for role in roles:
                missing = required - set(role.keys())
                assert not missing, f"{team}/{role.get('name')}: нет полей {missing}"

    def test_role_names_unique_within_team(self):
        for team, roles in TEAM_REGISTRY.items():
            names = [r["name"] for r in roles]
            assert len(names) == len(set(names)), f"{team}: дублирующиеся имена ролей"


# ---------------------------------------------------------------------------
# resolve_team_name / list_teams
# ---------------------------------------------------------------------------

class TestResolveTeamName:
    def test_canonical_name(self):
        assert resolve_team_name("traders") == "traders"
        assert resolve_team_name("coders") == "coders"
        assert resolve_team_name("analysts") == "analysts"
        assert resolve_team_name("creative") == "creative"

    def test_alias_resolution(self):
        assert resolve_team_name("трейдеры") == "traders"
        assert resolve_team_name("кодеры") == "coders"
        assert resolve_team_name("аналитика") == "analysts"
        assert resolve_team_name("креатив") == "creative"
        assert resolve_team_name("dev") == "coders"

    def test_case_insensitive(self):
        assert resolve_team_name("TRADERS") == "traders"
        assert resolve_team_name("Coders") == "coders"

    def test_whitespace_stripped(self):
        assert resolve_team_name("  traders  ") == "traders"

    def test_unknown_returns_none(self):
        assert resolve_team_name("nonexistent") is None
        assert resolve_team_name("") is None

    def test_all_aliases_resolve(self):
        """Все псевдонимы указывают на существующую команду."""
        for alias, target in TEAM_ALIASES.items():
            assert target in TEAM_REGISTRY, f"Псевдоним '{alias}' -> '{target}' не в реестре"


class TestListTeams:
    def test_contains_all_teams(self):
        result = list_teams()
        for team in TEAM_REGISTRY:
            assert team in result

    def test_contains_usage_hint(self):
        result = list_teams()
        assert "!swarm" in result


# ---------------------------------------------------------------------------
# SwarmBus
# ---------------------------------------------------------------------------

class TestSwarmBus:
    def test_initial_state(self):
        bus = SwarmBus()
        assert bus.active_count() == 0

    @pytest.mark.asyncio
    async def test_dispatch_unknown_team(self):
        """Неизвестная команда — возврат ошибки без исключения."""
        bus = SwarmBus()
        result = await bus.dispatch(
            source_team="traders",
            target_team="nonexistent",
            topic="test",
            router_factory=MagicMock(),
        )
        assert "не найдена" in result

    @pytest.mark.asyncio
    async def test_dispatch_max_depth(self):
        """Превышение глубины делегирования."""
        bus = SwarmBus()
        result = await bus.dispatch(
            source_team="traders",
            target_team="coders",
            topic="test",
            router_factory=MagicMock(),
            depth=bus._MAX_DEPTH,  # уже на лимите
        )
        assert "отклонено" in result

    @pytest.mark.asyncio
    async def test_dispatch_calls_agent_room(self):
        """Корректный dispatch создаёт AgentRoom и запускает run_round."""
        bus = SwarmBus()
        mock_router = AsyncMock()
        mock_router.route_query = AsyncMock(return_value="ответ роли")
        router_factory = MagicMock(return_value=mock_router)

        # Мокаем swarm_channels и swarm_memory чтобы не трогать singleton-стейт
        with patch("src.core.swarm.swarm_channels") as mock_ch, \
             patch("src.core.swarm.swarm_memory") as mock_mem:
            mock_ch.get_pending_intervention.return_value = None
            mock_ch.broadcast_round_start = AsyncMock()
            mock_ch.broadcast_role_step = AsyncMock()
            mock_ch.broadcast_round_end = AsyncMock()
            mock_mem.get_context_for_injection.return_value = ""

            result = await bus.dispatch(
                source_team="traders",
                target_team="coders",
                topic="напиши бот",
                router_factory=router_factory,
                depth=0,
            )

        assert "Swarm Room" in result
        assert mock_router.route_query.await_count >= 1
        assert bus.active_count() == 0  # задача завершена


class TestSwarmBusTask:
    def test_defaults(self):
        task = SwarmBusTask()
        assert len(task.task_id) == 8
        assert task.result is None
        assert task.error is None
        assert not task.done_event.is_set()


# ---------------------------------------------------------------------------
# AgentRoom
# ---------------------------------------------------------------------------

class TestAgentRoom:
    def test_default_roles(self):
        room = AgentRoom()
        assert room.roles is DEFAULT_AGENT_ROLES
        assert len(room.roles) == 3

    def test_custom_roles(self):
        custom = [{"name": "test", "emoji": "T", "title": "Тест", "system_hint": "hint"}]
        room = AgentRoom(roles=custom)
        assert room.roles is custom

    def test_role_context_clip_minimum(self):
        """Минимальный клип — 200 символов."""
        room = AgentRoom(role_context_clip=10)
        assert room.role_context_clip == 200

    @pytest.mark.asyncio
    async def test_run_round_collects_all_roles(self):
        """run_round вызывает router для каждой роли и собирает результаты."""
        roles = [
            {"name": "alpha", "emoji": "A", "title": "Альфа", "system_hint": "подсказка А"},
            {"name": "beta", "emoji": "B", "title": "Бета", "system_hint": "подсказка Б"},
        ]
        room = AgentRoom(roles=roles)
        mock_router = AsyncMock()
        mock_router.route_query = AsyncMock(side_effect=["ответ альфа", "ответ бета"])

        with patch("src.core.swarm.swarm_channels") as mock_ch, \
             patch("src.core.swarm.swarm_memory"):
            mock_ch.get_pending_intervention.return_value = None
            mock_ch.broadcast_round_start = AsyncMock()
            mock_ch.broadcast_role_step = AsyncMock()
            mock_ch.broadcast_round_end = AsyncMock()

            result = await room.run_round("тема теста", mock_router)

        assert mock_router.route_query.await_count == 2
        assert "ответ альфа" in result
        assert "ответ бета" in result

    @pytest.mark.asyncio
    async def test_run_round_handles_role_failure(self):
        """Если router бросает исключение, роль возвращает ошибку без падения."""
        roles = [{"name": "fail_role", "emoji": "X", "title": "Сбой", "system_hint": "hint"}]
        room = AgentRoom(roles=roles)
        mock_router = AsyncMock()
        mock_router.route_query = AsyncMock(side_effect=RuntimeError("model timeout"))

        with patch("src.core.swarm.swarm_channels") as mock_ch, \
             patch("src.core.swarm.swarm_memory"):
            mock_ch.get_pending_intervention.return_value = None
            mock_ch.broadcast_round_start = AsyncMock()
            mock_ch.broadcast_role_step = AsyncMock()
            mock_ch.broadcast_round_end = AsyncMock()

            result = await room.run_round("тема", mock_router)

        assert "Ошибка роли" in result
        assert "model timeout" in result

    @pytest.mark.asyncio
    async def test_run_round_clips_long_response(self):
        """Длинный ответ роли обрезается до role_context_clip."""
        roles = [{"name": "verbose", "emoji": "V", "title": "Болтун", "system_hint": ""}]
        room = AgentRoom(roles=roles, role_context_clip=200)
        mock_router = AsyncMock()
        mock_router.route_query = AsyncMock(return_value="x" * 500)

        with patch("src.core.swarm.swarm_channels") as mock_ch, \
             patch("src.core.swarm.swarm_memory"):
            mock_ch.get_pending_intervention.return_value = None
            mock_ch.broadcast_round_start = AsyncMock()
            mock_ch.broadcast_role_step = AsyncMock()
            mock_ch.broadcast_round_end = AsyncMock()

            result = await room.run_round("тема", mock_router)

        # Ответ в body обрезан (200 символов)
        body_section = result.split("**V Болтун:**\n")[1]
        assert len(body_section.strip()) <= 200


# ---------------------------------------------------------------------------
# _DELEGATE_PATTERN
# ---------------------------------------------------------------------------

class TestDelegatePattern:
    def test_matches_english(self):
        m = _DELEGATE_PATTERN.search("[DELEGATE: coders]")
        assert m and m.group(1).strip() == "coders"

    def test_matches_russian(self):
        m = _DELEGATE_PATTERN.search("[DELEGATE: аналитика]")
        assert m and m.group(1).strip() == "аналитика"

    def test_no_space_after_colon(self):
        m = _DELEGATE_PATTERN.search("[DELEGATE:traders]")
        assert m and m.group(1).strip() == "traders"

    def test_case_insensitive(self):
        m = _DELEGATE_PATTERN.search("[delegate: Coders]")
        assert m and m.group(1).strip() == "Coders"

    def test_no_match(self):
        assert _DELEGATE_PATTERN.search("просто текст") is None


# ---------------------------------------------------------------------------
# SwarmOrchestrator (базовый)
# ---------------------------------------------------------------------------

class TestSwarmOrchestrator:
    @pytest.mark.asyncio
    async def test_execute_parallel(self):
        orch = SwarmOrchestrator(tool_handler=MagicMock())
        tasks = [
            SwarmTask("a", lambda: "result_a"),
            SwarmTask("b", lambda: "result_b"),
        ]
        results = await orch.execute_parallel(tasks)
        assert results == {"a": "result_a", "b": "result_b"}

    @pytest.mark.asyncio
    async def test_execute_parallel_with_error(self):
        """Ошибка в одной задаче не роняет остальные."""
        orch = SwarmOrchestrator(tool_handler=MagicMock())

        def failing():
            raise ValueError("boom")

        tasks = [
            SwarmTask("ok", lambda: "fine"),
            SwarmTask("fail", failing),
        ]
        results = await orch.execute_parallel(tasks)
        assert results["ok"] == "fine"
        assert "Error" in results["fail"]
