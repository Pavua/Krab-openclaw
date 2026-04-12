# -*- coding: utf-8 -*-
"""
tests/test_r17_agent_room.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
R17: Тесты Multi-Agent Room MVP — AgentRoom в src/core/swarm.py.
Проверяет последовательную оркестрацию 3 ролей через мок-роутер.
"""

from unittest.mock import AsyncMock

import pytest

from src.core.swarm import AgentRoom


@pytest.mark.asyncio
async def test_agent_room_basic_round():
    """AgentRoom вызывает route_query для каждой роли и возвращает агрегированный ответ."""
    call_log = []

    async def mock_route_query(prompt, skip_swarm=False):
        # Фиксируем какая подсказка была передана
        call_log.append(prompt)
        # Возвращаем имитацию ответа на основе порядкового номера вызова
        idx = len(call_log)
        return f"Ответ_роли_{idx}"

    mock_router = AsyncMock()
    mock_router.route_query = mock_route_query

    room = AgentRoom()
    result = await room.run_round("Будущее AI", mock_router)

    # Все 3 роли должны быть вызваны
    assert len(call_log) == 3

    # Ответ должен содержать заголовок и результаты всех ролей
    assert "Swarm Room" in result
    assert "Будущее AI" in result
    assert "Аналитик" in result
    assert "Критик" in result
    assert "Интегратор" in result

    # Результаты предыдущих ролей передаются следующим
    assert "Ответ_роли_1" in call_log[1]  # критик видит ответ аналитика
    assert "Ответ_роли_2" in call_log[2]  # интегратор видит оба предыдущих


@pytest.mark.asyncio
async def test_agent_room_context_accumulation():
    """Каждая роль получает накопленный контекст предыдущих ролей."""
    contexts = []

    async def capture_context(prompt, skip_swarm=False):
        contexts.append(prompt)
        return f"result_{len(contexts)}"

    mock_router = AsyncMock()
    mock_router.route_query = capture_context

    room = AgentRoom()
    await room.run_round("Крипто-рынок 2026", mock_router)

    # Первый вызов (аналитик) — без накопленного контекста
    assert "Контекст предыдущих ролей" not in contexts[0]

    # Второй вызов (критик) — с контекстом аналитика
    assert "Контекст предыдущих ролей" in contexts[1]
    assert "result_1" in contexts[1]

    # Третий вызов (интегратор) — с контекстом аналитика И критика
    assert "result_1" in contexts[2]
    assert "result_2" in contexts[2]


@pytest.mark.asyncio
async def test_agent_room_custom_roles():
    """AgentRoom работает с кастомными ролями."""
    custom_roles = [
        {"name": "planner", "emoji": "📋", "title": "Планировщик", "system_hint": "Составь план."},
        {
            "name": "executor",
            "emoji": "⚡",
            "title": "Исполнитель",
            "system_hint": "Реализуй план.",
        },
    ]

    call_count = 0

    async def mock_query(prompt, skip_swarm=False):
        nonlocal call_count
        call_count += 1
        return f"custom_result_{call_count}"

    mock_router = AsyncMock()
    mock_router.route_query = mock_query

    room = AgentRoom(roles=custom_roles)
    result = await room.run_round("Задача", mock_router)

    assert call_count == 2  # Только 2 роли
    assert "Планировщик" in result
    assert "Исполнитель" in result


@pytest.mark.asyncio
async def test_agent_room_role_error_handled():
    """Ошибка в одной роли не ломает весь раунд — остальные роли выполняются."""
    call_count = 0

    async def failing_then_ok(prompt, skip_swarm=False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Тестовая ошибка аналитика")
        return f"ok_result_{call_count}"

    mock_router = AsyncMock()
    mock_router.route_query = failing_then_ok

    room = AgentRoom()
    result = await room.run_round("тест ошибки", mock_router)

    # Все 3 роли были попытаны
    assert call_count == 3
    # Ошибка первой роли отражена в результате
    assert "Ошибка роли" in result or "Аналитик" in result
    # Остальные роли отработали
    assert "ok_result_2" in result or "ok_result_3" in result
