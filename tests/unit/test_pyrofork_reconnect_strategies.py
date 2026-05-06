"""
Wave 33-A: тесты стратегий reconnect для pyrofork 2.3.69.

Покрывает _try_reconnect_pyrofork:
- Стратегия 1 (stop+start) при is_connected=True
- is_connected=False — прямой start без stop
- Стратегия 2 (Ping fallback) при падении стратегии 1
- Обе стратегии падают → returns False
- ConnectionError из stop() НЕ propagates наружу
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bridge() -> object:
    """Создаёт stub KraabUserbot без вызова __init__."""
    from src.userbot_bridge import KraabUserbot  # noqa: PLC0415

    bridge = KraabUserbot.__new__(KraabUserbot)
    return bridge


def _make_client(*, is_connected: bool) -> MagicMock:
    """Mock pyrofork Client с минимальным API."""
    client = MagicMock()
    client.is_connected = is_connected
    client.stop = AsyncMock()
    client.start = AsyncMock()
    client.invoke = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestTryReconnectPyrofork:
    """Тесты для KraabUserbot._try_reconnect_pyrofork (Wave 33-A)."""

    @pytest.mark.asyncio
    async def test_strategy1_stop_start_when_connected(self):
        """is_connected=True → stop(block=True) затем start() → True."""
        bridge = _make_bridge()
        client = _make_client(is_connected=True)

        result = await bridge._try_reconnect_pyrofork(client)

        assert result is True
        client.stop.assert_awaited_once_with(block=True)
        client.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_strategy1_start_directly_when_not_connected(self):
        """is_connected=False → start() без stop() → True."""
        bridge = _make_bridge()
        client = _make_client(is_connected=False)

        result = await bridge._try_reconnect_pyrofork(client)

        assert result is True
        client.stop.assert_not_awaited()
        client.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_strategy2_ping_fallback_when_strategy1_fails(self):
        """stop/start бросают ConnectionError → fallback Ping invoke → True."""
        bridge = _make_bridge()
        client = _make_client(is_connected=True)
        # stop падает — имитируем pyrofork "Can't disconnect an initialized client"
        client.stop.side_effect = ConnectionError("Can't disconnect an initialized client")
        # Ping invoke успешен
        client.invoke.return_value = MagicMock()

        mock_ping_cls = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {"pyrogram.raw.functions": MagicMock(Ping=mock_ping_cls)},
        ):
            result = await bridge._try_reconnect_pyrofork(client)

        assert result is True
        client.invoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_strategies_fail_returns_false(self):
        """Обе стратегии падают → returns False без исключений."""
        bridge = _make_bridge()
        client = _make_client(is_connected=True)
        client.stop.side_effect = ConnectionError("stop failed")
        client.invoke.side_effect = OSError("network unreachable")

        mock_ping_cls = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {"pyrogram.raw.functions": MagicMock(Ping=mock_ping_cls)},
        ):
            result = await bridge._try_reconnect_pyrofork(client)

        assert result is False

    @pytest.mark.asyncio
    async def test_connection_error_from_stop_does_not_propagate(self):
        """ConnectionError из stop() не propagates — метод всегда возвращает bool."""
        bridge = _make_bridge()
        client = _make_client(is_connected=True)
        # stop и start падают
        client.stop.side_effect = ConnectionError("initialized client")
        client.start.side_effect = RuntimeError("not started")
        # invoke тоже падает
        client.invoke.side_effect = Exception("invoke fail")

        mock_ping_cls = MagicMock(return_value=MagicMock())
        with patch.dict(
            "sys.modules",
            {"pyrogram.raw.functions": MagicMock(Ping=mock_ping_cls)},
        ):
            # Не должно бросить исключение наружу
            result = await bridge._try_reconnect_pyrofork(client)

        assert result is False
