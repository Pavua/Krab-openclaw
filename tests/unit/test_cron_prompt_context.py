"""Тесты для cron prompt context augmentation.

Цель: убедиться что _run_cron_prompt_and_send префиксит prompt готовым
RUNTIME CONTEXT блоком (cost/inbox/archive/reminders), чтобы LLM мог
ответить в один shot без tool-chain (раньше упирался в 90s timeout).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.userbot_bridge import KraabUserbot


def _make_bridge() -> KraabUserbot:
    """Создаёт минимальный bridge без вызова реального __init__."""
    bridge = KraabUserbot.__new__(KraabUserbot)
    bridge.client = MagicMock()
    bridge.client.is_connected = True
    bridge.client.send_message = AsyncMock()
    bridge.me = SimpleNamespace(id=12345)
    return bridge


@pytest.mark.asyncio
async def test_build_cron_context_returns_block_with_known_sections() -> None:
    bridge = _make_bridge()
    ctx = await bridge._build_cron_context()
    # Проверяем что блок содержит маркеры разделов
    assert "RUNTIME CONTEXT" in ctx
    assert "Расходы:" in ctx
    assert "Inbox:" in ctx
    assert "Archive.db" in ctx
    assert "Reminders" in ctx
    assert "END CONTEXT" in ctx
    # Размер ≤ ~3000 символов (≈500 токенов с запасом)
    assert len(ctx) < 3000


@pytest.mark.asyncio
async def test_build_cron_context_resilient_to_source_failures() -> None:
    """Если источник падает, секция помечается n/a без падения всего блока."""
    bridge = _make_bridge()
    with patch(
        "src.core.inbox_service.inbox_service.list_items",
        side_effect=RuntimeError("boom"),
    ):
        ctx = await bridge._build_cron_context()
    assert "Inbox: n/a" in ctx
    assert "RUNTIME CONTEXT" in ctx  # остальной блок собрался


@pytest.mark.asyncio
async def test_run_cron_prompt_augments_with_context_before_llm() -> None:
    """LLM должен получить prompt с префиксом RUNTIME CONTEXT (one-shot path)."""
    bridge = _make_bridge()

    captured_prompt: dict[str, str] = {}

    async def fake_route(prompt: str) -> str:
        captured_prompt["value"] = prompt
        return "OK reply"

    fake_adapter = SimpleNamespace(route_query=fake_route)

    with (
        patch.object(
            bridge,
            "_build_cron_system_prompt",
            return_value="cron system",
        ),
        patch.object(
            bridge,
            "_build_cron_context",
            new=AsyncMock(return_value="=== KRAB RUNTIME CONTEXT ===\nCost: $0\n=== END ==="),
        ),
        patch(
            "src.handlers.command_handlers._AgentRoomRouterAdapter",
            return_value=fake_adapter,
        ),
        patch.object(bridge, "_split_message", return_value=["OK reply"]),
    ):
        await bridge._run_cron_prompt_and_send("cron_native", "Brief: расходы дня")

    assert "RUNTIME CONTEXT" in captured_prompt["value"]
    assert "Brief: расходы дня" in captured_prompt["value"]
    # prompt идёт ПОСЛЕ context-блока
    assert captured_prompt["value"].index("RUNTIME CONTEXT") < captured_prompt["value"].index(
        "Brief: расходы дня"
    )
    bridge.client.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_run_cron_prompt_no_reply_short_circuits() -> None:
    """Если LLM ответил NO_REPLY — никаких send_message не должно быть."""
    bridge = _make_bridge()

    fake_adapter = SimpleNamespace(route_query=AsyncMock(return_value="NO_REPLY"))
    with (
        patch.object(bridge, "_build_cron_system_prompt", return_value="cron system"),
        patch.object(bridge, "_build_cron_context", new=AsyncMock(return_value="ctx")),
        patch(
            "src.handlers.command_handlers._AgentRoomRouterAdapter",
            return_value=fake_adapter,
        ),
    ):
        await bridge._run_cron_prompt_and_send("cron_native", "check")

    bridge.client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_cron_prompt_falls_back_when_context_build_fails() -> None:
    """Если _build_cron_context падает — все равно делаем LLM-вызов с raw prompt."""
    bridge = _make_bridge()

    captured: dict[str, str] = {}

    async def fake_route(prompt: str) -> str:
        captured["value"] = prompt
        return "ok"

    fake_adapter = SimpleNamespace(route_query=fake_route)

    with (
        patch.object(bridge, "_build_cron_system_prompt", return_value="cron system"),
        patch.object(
            bridge,
            "_build_cron_context",
            new=AsyncMock(side_effect=RuntimeError("ctx-fail")),
        ),
        patch(
            "src.handlers.command_handlers._AgentRoomRouterAdapter",
            return_value=fake_adapter,
        ),
        patch.object(bridge, "_split_message", return_value=["ok"]),
    ):
        await bridge._run_cron_prompt_and_send("cron_native", "raw prompt body")

    # Контекст не добавился, но prompt прошёл as-is
    assert captured["value"] == "raw prompt body"


def test_build_cron_system_prompt_minimal_and_no_tools() -> None:
    """Cron system_prompt должен быть короткий и не упоминать tools."""
    p = KraabUserbot._build_cron_system_prompt()
    assert len(p) < 500, f"cron prompt too long: {len(p)}"
    assert "NO_REPLY" in p
    # Запрет на tool calls должен быть явно прописан (negative instruction)
    low = p.lower()
    assert "никаких tool" in low or "no tool" in low


@pytest.mark.asyncio
async def test_run_cron_prompt_silent_skip_on_gateway_placeholder() -> None:
    """Если LLM/gateway вернул 'No response from OpenClaw.' — silent skip."""
    bridge = _make_bridge()

    fake_adapter = SimpleNamespace(
        route_query=AsyncMock(return_value="No response from OpenClaw.")
    )
    with (
        patch.object(bridge, "_build_cron_system_prompt", return_value="cron system"),
        patch.object(bridge, "_build_cron_context", new=AsyncMock(return_value="ctx")),
        patch(
            "src.handlers.command_handlers._AgentRoomRouterAdapter",
            return_value=fake_adapter,
        ),
    ):
        await bridge._run_cron_prompt_and_send("cron_native", "check")

    bridge.client.send_message.assert_not_awaited()
