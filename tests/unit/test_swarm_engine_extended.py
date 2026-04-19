# -*- coding: utf-8 -*-
"""
tests/unit/test_swarm_engine_extended.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Расширенные тесты AgentRoom и _DELEGATE_PATTERN из src/core/swarm.py.

Фокус на:
- построение prompt для роли (tool hint, context, тема)
- накопление контекста между ролями
- парсинг _DELEGATE_PATTERN (различные форматы)
- форматирование итогового результата
- intervention injection от владельца
- делегирование без router_factory (не должно падать)
- поведение run_loop при topic evolution
- SwarmOrchestrator._resolve_maybe_awaitable напрямую
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm import (
    _DELEGATE_PATTERN,
    AgentRoom,
    SwarmOrchestrator,
)

# ---------------------------------------------------------------------------
# Пути патчей
# ---------------------------------------------------------------------------

_MEMORY_PATH = "src.core.swarm.swarm_memory"
_CHANNELS_PATH = "src.core.swarm.swarm_channels"


def _make_memory_mock(context: str = "") -> MagicMock:
    """Мок swarm_memory с опциональным инжектируемым контекстом."""
    m = MagicMock()
    m.get_context_for_injection.return_value = context
    m.save_run = MagicMock()
    return m


def _make_channels_mock() -> MagicMock:
    """Мок swarm_channels — все broadcast-методы async."""
    c = MagicMock()
    c.mark_round_active = MagicMock()
    c.mark_round_done = MagicMock()
    c.get_pending_intervention.return_value = ""
    c.broadcast_round_start = AsyncMock()
    c.broadcast_round_end = AsyncMock()
    c.broadcast_role_step = AsyncMock()
    c.broadcast_delegation = AsyncMock()
    return c


def _make_router(response: str = "Ответ") -> MagicMock:
    """Простой мок-роутер."""
    r = MagicMock()
    r.route_query = AsyncMock(return_value=response)
    return r


# ---------------------------------------------------------------------------
# _DELEGATE_PATTERN — парсинг директивы делегирования
# ---------------------------------------------------------------------------


class TestDelegatePattern:
    """Проверяем regex _DELEGATE_PATTERN на разных форматах строк."""

    def test_basic_match(self):
        """[DELEGATE: coders] распознаётся."""
        m = _DELEGATE_PATTERN.search("[DELEGATE: coders] напиши бота")
        assert m is not None
        assert m.group(1).strip() == "coders"

    def test_no_space_match(self):
        """[DELEGATE:traders] без пробела тоже распознаётся."""
        m = _DELEGATE_PATTERN.search("[DELEGATE:traders]")
        assert m is not None
        assert m.group(1).strip() == "traders"

    def test_extra_spaces_match(self):
        """Паттерн допускает один пробел после двоеточия ([DELEGATE: team]) — это штатный формат."""
        # Паттерн использует \s* — один пробел допустим, множественные — нет
        m = _DELEGATE_PATTERN.search("[DELEGATE: analysts]")
        assert m is not None
        assert m.group(1).strip() == "analysts"

    def test_cyrillic_team_match(self):
        """Кириллическое название команды поддерживается."""
        m = _DELEGATE_PATTERN.search("[DELEGATE: аналитика]")
        assert m is not None
        assert m.group(1).strip() == "аналитика"

    def test_case_insensitive(self):
        """Директива нечувствительна к регистру."""
        m = _DELEGATE_PATTERN.search("[delegate: creative]")
        assert m is not None

    def test_no_match_missing_brackets(self):
        """Без квадратных скобок паттерн не срабатывает."""
        m = _DELEGATE_PATTERN.search("DELEGATE: coders")
        assert m is None

    def test_sub_removes_directive(self):
        """Подстановка sub удаляет директиву из строки."""
        cleaned = _DELEGATE_PATTERN.sub("", "[DELEGATE: coders] реши задачу").strip()
        assert "DELEGATE" not in cleaned
        assert "реши задачу" in cleaned


# ---------------------------------------------------------------------------
# Построение промпта: первая роль ОБЯЗАНА вызвать web_search
# ---------------------------------------------------------------------------


class TestPromptComposition:
    """Проверяем, что промпт содержит нужные компоненты."""

    @pytest.mark.asyncio
    async def test_first_role_prompt_contains_web_search_mandatory(self):
        """Промпт первой роли должен содержать ОБЯЗАН + web_search."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Ответ"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", router)

        first_prompt = captured_prompts[0]
        assert "web_search" in first_prompt
        assert "ОБЯЗАН" in first_prompt

    @pytest.mark.asyncio
    async def test_subsequent_roles_prompt_optional_web_search(self):
        """Промпт второй и третьей роли не требует обязательного web_search."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Ответ"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", router)

        # Второй и третий промпты не содержат «ОБЯЗАН»
        for prompt in captured_prompts[1:]:
            assert "ОБЯЗАН" not in prompt

    @pytest.mark.asyncio
    async def test_second_role_prompt_contains_previous_context(self):
        """Промпт второй роли включает контекст ответа первой роли."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Уникальный_ответ_первой_роли"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", router)

        # Второй промпт должен содержать ответ первой роли
        second_prompt = captured_prompts[1]
        assert "Уникальный_ответ_первой_роли" in second_prompt

    @pytest.mark.asyncio
    async def test_prompt_contains_topic(self):
        """Промпт каждой роли содержит тему."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Ответ"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("Уникальная_тема_XYZ", router)

        for prompt in captured_prompts:
            assert "Уникальная_тема_XYZ" in prompt

    @pytest.mark.asyncio
    async def test_prompt_contains_system_hint(self):
        """Промпт содержит system_hint из роли."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Ответ"

        router = MagicMock()
        router.route_query = capture_route

        custom_roles = [
            {"name": "r1", "emoji": "X", "title": "Роль1", "system_hint": "УНИКАЛЬНЫЙ_ХИНТ_42"}
        ]

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom(roles=custom_roles)
            await room.run_round("тест", router)

        assert "УНИКАЛЬНЫЙ_ХИНТ_42" in captured_prompts[0]


# ---------------------------------------------------------------------------
# Intervention injection от владельца
# ---------------------------------------------------------------------------


class TestInterventionInjection:
    """Вмешательство владельца в ходе раунда."""

    @pytest.mark.asyncio
    async def test_intervention_injected_into_context(self):
        """Если get_pending_intervention возвращает текст — он попадает в контекст следующей роли."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()

        # Первая роль: intervention есть; остальные — нет
        intervention_texts = ["ВМЕШАТЕЛЬСТВО_ТЕКСТ", "", ""]
        ch.get_pending_intervention.side_effect = intervention_texts

        captured_prompts: list[str] = []

        async def capture_route(prompt, skip_swarm=False):
            captured_prompts.append(prompt)
            return "Ответ роли"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", router, _team_name="traders")

        # Текст вмешательства должен попасть во второй или третий промпт
        found = any("ВМЕШАТЕЛЬСТВО_ТЕКСТ" in p for p in captured_prompts[1:])
        assert found

    @pytest.mark.asyncio
    async def test_no_intervention_without_team_name(self):
        """Без _team_name get_pending_intervention не вызывается."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            router = _make_router("Ответ")
            await room.run_round("тест", router)

        ch.get_pending_intervention.assert_not_called()


# ---------------------------------------------------------------------------
# Форматирование результата
# ---------------------------------------------------------------------------


class TestResultFormatting:
    """Структура итогового строкового результата."""

    @pytest.mark.asyncio
    async def test_result_starts_with_swarm_room_header(self):
        """Результат начинается с заголовка '🐝 **Swarm Room:'."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_round("Биткоин", _make_router("ОК"))
        assert result.startswith("🐝 **Swarm Room: Биткоин**")

    @pytest.mark.asyncio
    async def test_result_contains_delegation_section_when_delegated(self):
        """При делегировании в результате присутствует секция Делегирование."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        call_count = 0

        async def _route(prompt, skip_swarm=False):
            nonlocal call_count
            call_count += 1
            return "[DELEGATE: coders] сделай" if call_count == 1 else "Ответ"

        router = MagicMock()
        router.route_query = _route

        bus = MagicMock()
        bus.dispatch = AsyncMock(return_value="Готово")
        rf = MagicMock(return_value=router)

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_round(
                "тема", router, _bus=bus, _router_factory=rf, _team_name="traders"
            )
        assert "Делегирование" in result

    @pytest.mark.asyncio
    async def test_result_no_delegation_section_without_delegation(self):
        """Без делегирования секция Делегирование отсутствует."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_round("тест", _make_router("Обычный ответ"))
        assert "Делегирование" not in result

    @pytest.mark.asyncio
    async def test_result_contains_role_emojis(self):
        """Результат содержит эмодзи ролей из DEFAULT_AGENT_ROLES."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_round("тест", _make_router("Ответ"))
        # Дефолтные роли имеют эмодзи 🔬, 🎯, 🧠
        assert "🔬" in result
        assert "🎯" in result
        assert "🧠" in result


# ---------------------------------------------------------------------------
# run_loop — эволюция темы между раундами
# ---------------------------------------------------------------------------


class TestRunLoopTopicEvolution:
    """Тема второго раунда включает итог первого."""

    @pytest.mark.asyncio
    async def test_second_round_topic_includes_first_result(self):
        """Второй раунд получает тему с 'Улучши' и клипом предыдущего результата."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()

        topics_seen: list[str] = []
        call_count = 0

        async def capture_route(prompt, skip_swarm=False):
            nonlocal call_count
            call_count += 1
            # Отслеживаем только первый вызов каждого раунда (аналитик)
            if call_count in (1, 4):  # роли 1,2,3 первого; 4,5,6 второго
                topics_seen.append(prompt)
            return "Ответ_раунда"

        router = MagicMock()
        router.route_query = capture_route

        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_loop("Исходная тема", router, rounds=2)

        # Промпт второго раунда содержит «Улучши»
        assert "Улучши" in topics_seen[1]
        assert "Исходная тема" in topics_seen[1]

    @pytest.mark.asyncio
    async def test_run_loop_header_contains_base_topic(self):
        """Заголовок Swarm Loop всегда содержит исходную тему."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            result = await room.run_loop("ИСХОДНАЯ_ТЕМА_999", _make_router("Ответ"), rounds=2)
        assert "ИСХОДНАЯ_ТЕМА_999" in result


# ---------------------------------------------------------------------------
# SwarmOrchestrator._resolve_maybe_awaitable
# ---------------------------------------------------------------------------


class TestResolveMaybeAwaitable:
    """Прямая проверка статического метода."""

    @pytest.mark.asyncio
    async def test_resolves_coroutine(self):
        async def coro():
            return "coro_value"

        result = await SwarmOrchestrator._resolve_maybe_awaitable(coro())
        assert result == "coro_value"

    @pytest.mark.asyncio
    async def test_passes_plain_value(self):
        result = await SwarmOrchestrator._resolve_maybe_awaitable(42)
        assert result == 42

    @pytest.mark.asyncio
    async def test_passes_none(self):
        result = await SwarmOrchestrator._resolve_maybe_awaitable(None)
        assert result is None


# ---------------------------------------------------------------------------
# Broadcast: broadcast_role_step вызывается для каждой роли
# ---------------------------------------------------------------------------


class TestBroadcastCoverage:
    """Проверяем, что broadcast_role_step вызывается для каждой роли."""

    @pytest.mark.asyncio
    async def test_broadcast_role_step_called_per_role(self):
        """broadcast_role_step должен быть вызван по разу на каждую роль."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", _make_router("Ответ"), _team_name="analysts")
        # 3 дефолтных роли — 3 вызова
        assert ch.broadcast_role_step.call_count == 3

    @pytest.mark.asyncio
    async def test_broadcast_not_called_without_team(self):
        """Без _team_name broadcast_role_step НЕ вызывается."""
        mem = _make_memory_mock()
        ch = _make_channels_mock()
        with patch(_MEMORY_PATH, mem), patch(_CHANNELS_PATH, ch):
            room = AgentRoom()
            await room.run_round("тест", _make_router("Ответ"))
        ch.broadcast_role_step.assert_not_called()
