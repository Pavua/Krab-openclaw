# -*- coding: utf-8 -*-
"""
Тесты для src/core/swarm.py.

Покрывает:
- SwarmTask — инициализация, хранение аргументов
- SwarmOrchestrator.execute_parallel — параллельное выполнение, обработка ошибок
- AgentRoom.__init__ — дефолтные и кастомные роли, clip
- AgentRoom.run_round — нормальный путь, пустой ответ, ошибка роли, делегирование
- AgentRoom.run_loop — несколько раундов, edge-case min/max
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm import (
    DEFAULT_AGENT_ROLES,
    AgentRoom,
    SwarmOrchestrator,
    SwarmTask,
)

# ---------------------------------------------------------------------------
# Патч-фикстуры для модульных синглтонов swarm_memory и swarm_channels
# ---------------------------------------------------------------------------

_MEMORY_PATH = "src.core.swarm.swarm_memory"
_CHANNELS_PATH = "src.core.swarm.swarm_channels"


def _make_memory_mock() -> MagicMock:
    """Создаёт мок swarm_memory."""
    m = MagicMock()
    m.get_context_for_injection.return_value = ""
    m.save_run = MagicMock()
    return m


def _make_channels_mock() -> MagicMock:
    """Создаёт мок swarm_channels."""
    c = MagicMock()
    c.mark_round_active = MagicMock()
    c.mark_round_done = MagicMock()
    c.get_pending_intervention.return_value = ""
    c.broadcast_round_start = AsyncMock()
    c.broadcast_round_end = AsyncMock()
    c.broadcast_role_step = AsyncMock()
    c.broadcast_delegation = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# Хелпер: минимальный роутер
# ---------------------------------------------------------------------------


def _make_router(response: str = "Ответ роли") -> MagicMock:
    """Создаёт мок-роутер с фиксированным ответом route_query."""
    router = MagicMock()
    router.route_query = AsyncMock(return_value=response)
    return router


# ---------------------------------------------------------------------------
# SwarmTask
# ---------------------------------------------------------------------------


class TestSwarmTask:
    """Инициализация задачи рабочего."""

    def test_stores_name_and_func(self):
        fn = MagicMock(return_value=42)
        task = SwarmTask("my_task", fn, "arg1", kw=True)
        assert task.name == "my_task"
        assert task.func is fn
        assert task.args == ("arg1",)
        assert task.kwargs == {"kw": True}

    def test_no_args(self):
        fn = MagicMock()
        task = SwarmTask("empty", fn)
        assert task.args == ()
        assert task.kwargs == {}


# ---------------------------------------------------------------------------
# SwarmOrchestrator
# ---------------------------------------------------------------------------


class TestSwarmOrchestrator:
    """Параллельное выполнение задач через execute_parallel."""

    def setup_method(self):
        self.tools = MagicMock()
        self.orch = SwarmOrchestrator(tool_handler=self.tools)

    @pytest.mark.asyncio
    async def test_execute_parallel_sync_tasks(self):
        """Синхронные функции корректно оборачиваются и выполняются."""
        tasks = [
            SwarmTask("a", lambda: "result_a"),
            SwarmTask("b", lambda: "result_b"),
        ]
        results = await self.orch.execute_parallel(tasks)
        assert results["a"] == "result_a"
        assert results["b"] == "result_b"

    @pytest.mark.asyncio
    async def test_execute_parallel_async_tasks(self):
        """Async-функции обрабатываются через _resolve_maybe_awaitable."""

        async def async_fn():
            return "async_result"

        tasks = [SwarmTask("c", async_fn)]
        results = await self.orch.execute_parallel(tasks)
        assert results["c"] == "async_result"

    @pytest.mark.asyncio
    async def test_execute_parallel_error_isolation(self):
        """Ошибка в одной задаче не ронает остальные."""

        def failing():
            raise ValueError("boom")

        tasks = [
            SwarmTask("ok", lambda: "fine"),
            SwarmTask("fail", failing),
        ]
        results = await self.orch.execute_parallel(tasks)
        assert results["ok"] == "fine"
        assert "Error" in results["fail"]
        assert "boom" in results["fail"]

    @pytest.mark.asyncio
    async def test_execute_parallel_empty(self):
        """Пустой список задач возвращает пустой словарь."""
        results = await self.orch.execute_parallel([])
        assert results == {}

    @pytest.mark.asyncio
    async def test_execute_parallel_args_forwarded(self):
        """Аргументы и kwargs правильно передаются в функцию."""

        def add(x, y):
            return x + y

        tasks = [SwarmTask("add", add, 3, y=7)]
        results = await self.orch.execute_parallel(tasks)
        assert results["add"] == 10


# ---------------------------------------------------------------------------
# AgentRoom — инициализация
# ---------------------------------------------------------------------------


class TestAgentRoomInit:
    """Инициализация AgentRoom с разными параметрами."""

    def test_default_roles_used(self):
        room = AgentRoom()
        assert room.roles is DEFAULT_AGENT_ROLES
        assert len(room.roles) == 3

    def test_custom_roles(self):
        custom = [{"name": "r1", "system_hint": "hint"}]
        room = AgentRoom(roles=custom)
        assert room.roles is custom

    def test_clip_respected(self):
        room = AgentRoom(role_context_clip=500)
        assert room.role_context_clip == 500

    def test_clip_minimum_enforced(self):
        """Clip не может быть меньше 200."""
        room = AgentRoom(role_context_clip=10)
        assert room.role_context_clip == 200

    def test_none_roles_uses_defaults(self):
        room = AgentRoom(roles=None)
        assert room.roles is DEFAULT_AGENT_ROLES


# ---------------------------------------------------------------------------
# AgentRoom.run_round — нормальный путь
# ---------------------------------------------------------------------------


class TestAgentRoomRunRound:
    """Запуск роевого раунда."""

    @pytest.mark.asyncio
    async def test_run_round_returns_string(self):
        """run_round возвращает строку с заголовком темы."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Анализ готов")
            result = await room.run_round("BTC цена", router)
        assert isinstance(result, str)
        assert "BTC цена" in result

    @pytest.mark.asyncio
    async def test_run_round_all_roles_called(self):
        """route_query вызывается для каждой роли (3 дефолтных)."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            await room.run_round("тест", router)
        assert router.route_query.call_count == 3

    @pytest.mark.asyncio
    async def test_run_round_contains_role_titles(self):
        """Результат содержит title каждой роли."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            result = await room.run_round("тест", router)
        assert "Аналитик" in result
        assert "Критик" in result
        assert "Интегратор" in result

    @pytest.mark.asyncio
    async def test_run_round_empty_response_replaced(self):
        """Пустой ответ роли заменяется заглушкой, не ронает раунд."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("")
            result = await room.run_round("тест", router)
        assert "Пустой ответ роли" in result

    @pytest.mark.asyncio
    async def test_run_round_role_exception_handled(self):
        """Исключение из route_query превращается в [Ошибка роли ...]."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = MagicMock()
            router.route_query = AsyncMock(side_effect=RuntimeError("LLM timeout"))
            result = await room.run_round("тест", router)
        assert "Ошибка роли" in result

    @pytest.mark.asyncio
    async def test_run_round_memory_injected(self):
        """Если команда указана и память не пуста — контекст инжектируется."""
        mem = _make_memory_mock()
        mem.get_context_for_injection.return_value = "Старый контекст: BTC $50k"
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("ОК")
            await room.run_round("BTC анализ", router, _team_name="traders")
        mem.get_context_for_injection.assert_called_once_with("traders")

    @pytest.mark.asyncio
    async def test_run_round_saves_memory_for_top_level(self):
        """save_run вызывается для top-level раунда (depth=0) с team_name."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Результат")
            await room.run_round("тема", router, _team_name="coders", _depth=0)
        mem.save_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_round_no_save_for_delegated(self):
        """save_run НЕ вызывается для делегированных раундов (depth>0)."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Результат")
            await room.run_round("тема", router, _team_name="coders", _depth=1)
        mem.save_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_round_custom_single_role(self):
        """Одна кастомная роль — один вызов route_query."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        custom_roles = [{"name": "solo", "emoji": "🎸", "title": "Соло", "system_hint": "Ты один"}]
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom(roles=custom_roles)
            router = _make_router("Соло-ответ")
            result = await room.run_round("тест", router)
        assert router.route_query.call_count == 1
        assert "Соло" in result

    @pytest.mark.asyncio
    async def test_run_round_clip_applied(self):
        """Клип обрезает длинный ответ роли в контексте следующих ролей."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        long_response = "x" * 10000
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom(role_context_clip=100)
            router = _make_router(long_response)
            result = await room.run_round("тест", router)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_run_round_delegation_detected(self):
        """[DELEGATE: coders] в ответе роли активирует dispatch в SwarmBus."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        call_count = 0

        # Первая роль возвращает директиву делегирования
        async def _route(prompt, skip_swarm=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "[DELEGATE: coders] напиши бота"
            return "Ответ без делегирования"

        router = MagicMock()
        router.route_query = _route

        bus = MagicMock()
        bus.dispatch = AsyncMock(return_value="Код написан")
        router_factory = MagicMock(return_value=router)

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_round(
                "напиши бота",
                router,
                _bus=bus,
                _router_factory=router_factory,
                _team_name="traders",
            )
        bus.dispatch.assert_called_once()
        assert "Делегирование" in result or "Код написан" in result

    @pytest.mark.asyncio
    async def test_run_round_no_delegation_without_bus(self):
        """Без _bus директива [DELEGATE:] игнорируется, dispatch не вызывается."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("[DELEGATE: coders] напиши бота")
            result = await room.run_round("тема", router)
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_run_round_broadcast_called_with_team(self):
        """Broadcast-методы вызываются когда указан _team_name."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            await room.run_round("тест", router, _team_name="analysts")
        ch.broadcast_round_start.assert_called_once()
        ch.broadcast_round_end.assert_called_once()


# ---------------------------------------------------------------------------
# AgentRoom.run_loop
# ---------------------------------------------------------------------------


class TestAgentRoomRunLoop:
    """Несколько раундов с итеративной доработкой."""

    @pytest.mark.asyncio
    async def test_run_loop_single_round(self):
        """rounds=1 — run_round вызывается один раз."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            result = await room.run_loop("тема", router, rounds=1)
        assert "Раунд 1/1" in result
        assert "Swarm Loop" in result

    @pytest.mark.asyncio
    async def test_run_loop_two_rounds(self):
        """rounds=2 — два раунда, оба в результате."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ раунда")
            result = await room.run_loop("тема", router, rounds=2)
        assert "Раунд 1/2" in result
        assert "Раунд 2/2" in result

    @pytest.mark.asyncio
    async def test_run_loop_max_rounds_cap(self):
        """rounds > max_rounds обрезается до max_rounds."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            result = await room.run_loop("тема", router, rounds=10, max_rounds=2)
        assert "Раунд 2/2" in result
        assert "Раунд 3" not in result

    @pytest.mark.asyncio
    async def test_run_loop_min_one_round(self):
        """rounds=0 приводится к 1."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            result = await room.run_loop("тема", router, rounds=0)
        assert "Раунд 1/1" in result

    @pytest.mark.asyncio
    async def test_run_loop_topic_in_header(self):
        """Заголовок Swarm Loop содержит исходную тему."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            result = await room.run_loop("Анализ рынка", router, rounds=1)
        assert "Анализ рынка" in result
