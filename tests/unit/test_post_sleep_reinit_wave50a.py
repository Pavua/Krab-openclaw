# -*- coding: utf-8 -*-
"""
Wave 50-A: idempotent client.disconnect для macOS post-sleep reinit.

Sentry incident: PYTHON-FASTAPI-88 macos_post_sleep_reinit_failed
"Client is already disconnected" — pyrofork raises ConnectionError когда
client.stop() вызывается на disconnected клиенте (после macOS sleep socket
уже порван системой).

Покрываем:
1. _safe_client_disconnect глотает ConnectionError ("already disconnected")
2. _safe_client_disconnect skips stop() когда is_connected=False
3. _safe_client_disconnect возвращает True при None client (no-op)
4. _force_pyrofork_session_reinit устойчив к stale client
5. _force_pyrofork_session_reinit — повторный вызов идемпотентен
6. Caller _macos_sleep_detect_loop логирует WARNING (Wave 41-O hygiene)
"""

from __future__ import annotations

import logging
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot.network_watchdog import NetworkWatchdogMixin

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_stub(*, client: MagicMock | None) -> types.SimpleNamespace:
    """Минимальный stub с привязанным методом NetworkWatchdogMixin."""
    stub = types.SimpleNamespace()
    stub.client = client
    stub._last_telegram_event_ts = 0.0
    # bound methods
    stub._safe_client_disconnect = NetworkWatchdogMixin._safe_client_disconnect
    stub._force_pyrofork_session_reinit = (
        NetworkWatchdogMixin._force_pyrofork_session_reinit.__get__(stub)
    )
    return stub


# ── _safe_client_disconnect ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_disconnect_handles_already_disconnected() -> None:
    """ConnectionError "already disconnected" не должен пробрасываться."""
    client = MagicMock(is_connected=True)
    client.stop = AsyncMock(side_effect=ConnectionError("Client is already disconnected"))

    result = await NetworkWatchdogMixin._safe_client_disconnect(client)

    assert result is True
    client.stop.assert_awaited_once_with(block=True)


@pytest.mark.asyncio
async def test_safe_disconnect_skips_when_not_connected() -> None:
    """is_connected=False → stop() не дёргаем (skip path)."""
    client = MagicMock(is_connected=False)
    client.stop = AsyncMock()

    result = await NetworkWatchdogMixin._safe_client_disconnect(client)

    assert result is True
    client.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_disconnect_handles_none_client() -> None:
    """client=None — no-op, return True."""
    result = await NetworkWatchdogMixin._safe_client_disconnect(None)
    assert result is True


@pytest.mark.asyncio
async def test_safe_disconnect_handles_missing_is_connected_attr() -> None:
    """Defensive getattr: pyrofork может убрать is_connected — fallback False."""
    # SimpleNamespace без is_connected
    client = types.SimpleNamespace()
    client.stop = AsyncMock()

    result = await NetworkWatchdogMixin._safe_client_disconnect(client)  # type: ignore[arg-type]

    # is_connected отсутствует → getattr → False → skip stop
    assert result is True
    client.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_safe_disconnect_returns_false_on_unexpected_error() -> None:
    """Не-ConnectionError — return False (не глотаем тихо)."""
    client = MagicMock(is_connected=True)
    client.stop = AsyncMock(side_effect=RuntimeError("event loop closed"))

    result = await NetworkWatchdogMixin._safe_client_disconnect(client)

    assert result is False


# ── _force_pyrofork_session_reinit ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_sleep_reinit_robust_to_stale_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale client (already disconnected) → reinit не падает."""
    # Убираем asyncio.sleep(2)
    import src.userbot.network_watchdog as nw

    monkeypatch.setattr(nw.asyncio, "sleep", AsyncMock())

    client = MagicMock(is_connected=True)
    client.stop = AsyncMock(side_effect=ConnectionError("Client is already disconnected"))
    client.start = AsyncMock()
    stub = _make_stub(client=client)

    # Не должно бросать
    await stub._force_pyrofork_session_reinit()

    # start() обязан быть вызван несмотря на ConnectionError при stop
    client.start.assert_awaited_once()
    assert stub._last_telegram_event_ts > 0


@pytest.mark.asyncio
async def test_post_sleep_reinit_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Двойной вызов подряд — не должно быть double-disconnect crash."""
    import src.userbot.network_watchdog as nw

    monkeypatch.setattr(nw.asyncio, "sleep", AsyncMock())

    # Первый вызов: connected → stop ok → start ok. После start,
    # is_connected остаётся True (mock не меняет state); _safe_disconnect
    # вызовет stop ещё раз — это ок, главное что не crash.
    client = MagicMock(is_connected=True)
    client.stop = AsyncMock()
    client.start = AsyncMock()
    stub = _make_stub(client=client)

    await stub._force_pyrofork_session_reinit()
    await stub._force_pyrofork_session_reinit()

    assert client.start.await_count == 2
    assert client.stop.await_count == 2


@pytest.mark.asyncio
async def test_reinit_logs_warning_not_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wave 41-O hygiene: failure → log WARNING, не ERROR.

    Patch'аем module-level logger напрямую (structlog не route'ится в caplog).
    """
    import src.userbot.network_watchdog as nw

    monkeypatch.setattr(nw.asyncio, "sleep", AsyncMock())

    captured: list[tuple[str, str]] = []

    fake_logger = MagicMock()
    fake_logger.warning = lambda event, **kw: captured.append(("warning", event))
    fake_logger.error = lambda event, **kw: captured.append(("error", event))
    fake_logger.info = lambda event, **kw: captured.append(("info", event))
    fake_logger.debug = lambda event, **kw: captured.append(("debug", event))
    monkeypatch.setattr(nw, "logger", fake_logger)

    client = MagicMock(is_connected=False)
    client.start = AsyncMock(side_effect=RuntimeError("DC unreachable"))
    stub = _make_stub(client=client)

    with pytest.raises(RuntimeError):
        await stub._force_pyrofork_session_reinit()

    failure_logs = [(lvl, ev) for lvl, ev in captured if ev == "forced_pyrofork_reinit_failed"]
    assert failure_logs, f"должен залогировать forced_pyrofork_reinit_failed, captured={captured}"
    assert all(lvl == "warning" for lvl, _ in failure_logs), (
        f"ожидался warning, получен {failure_logs}"
    )
    # Wave 41-O: не должно быть ERROR-ов
    assert not [c for c in captured if c[0] == "error"], f"unexpected ERROR-логи: {captured}"


@pytest.mark.asyncio
async def test_reinit_with_none_client_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """self.client=None — reinit gracefully skips (sleep period before client init)."""
    import src.userbot.network_watchdog as nw

    monkeypatch.setattr(nw.asyncio, "sleep", AsyncMock())

    stub = _make_stub(client=None)

    # Не должно бросать — _safe_disconnect None → True, start() skipped (if self.client)
    await stub._force_pyrofork_session_reinit()

    assert stub._last_telegram_event_ts > 0
