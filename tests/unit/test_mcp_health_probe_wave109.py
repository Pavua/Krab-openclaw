# -*- coding: utf-8 -*-
"""Wave 109: tests для MCPHealthProbe.

Покрытие:
- success probe → last_ok=True, consecutive_fails сбрасывается.
- timeout probe → last_ok=False, reason='timeout', counter растёт.
- exception probe → reason='exception', last_error содержит repr.
- no_tools (пустой list_tools) → reason='no_tools'.
- consecutive_fails возрастает при подряд провалах и сбрасывается на ok.
- get_snapshot возвращает копию (caller не мутирует state).
- managed серверы без активной сессии помечаются reason='no_session'.
- _env_interval graceful на мусоре/негативе.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.core.mcp_health_probe import MCPHealthProbe, _env_interval


class _StubSession:
    """Минимальный session mock с настраиваемым list_tools поведением."""

    def __init__(self, *, tools: list[Any] | None = None, raises: Exception | None = None,
                 sleep_sec: float = 0.0):
        self._tools = tools if tools is not None else [object()]
        self._raises = raises
        self._sleep_sec = sleep_sec

    async def list_tools(self) -> Any:
        if self._sleep_sec:
            await asyncio.sleep(self._sleep_sec)
        if self._raises is not None:
            raise self._raises

        class _R:
            tools = self._tools

        return _R()


class _StubManager:
    def __init__(self, sessions: dict[str, Any] | None = None):
        self.sessions = dict(sessions or {})


@pytest.fixture
def fake_clock():
    """Mutable [ts] чтобы тесты двигали время монотонно."""
    return [1_000_000.0]


def _make_probe(manager: _StubManager, fake_clock: list[float], **kw: Any) -> MCPHealthProbe:
    return MCPHealthProbe(
        manager_fn=lambda: manager,
        now_fn=lambda: fake_clock[0],
        timeout_sec=kw.get("timeout_sec", 0.2),
    )


@pytest.mark.asyncio
async def test_probe_success_records_ok(fake_clock, monkeypatch):
    manager = _StubManager({"telegram": _StubSession()})
    probe = _make_probe(manager, fake_clock)
    # Изолируем от настоящего реестра — пусть managed list будет пуст.
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"telegram": {}},
    )

    snapshot = await probe.probe_once()

    assert "telegram" in snapshot
    assert snapshot["telegram"]["last_ok"] is True
    assert snapshot["telegram"]["consecutive_fails"] == 0
    assert snapshot["telegram"]["last_reason"] == "ok"
    assert snapshot["telegram"]["last_probe_ts"] == fake_clock[0]


@pytest.mark.asyncio
async def test_probe_timeout_records_failure(fake_clock, monkeypatch):
    # sleep_sec > timeout → asyncio.TimeoutError
    manager = _StubManager({"slowsrv": _StubSession(sleep_sec=1.0)})
    probe = _make_probe(manager, fake_clock, timeout_sec=0.05)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"slowsrv": {}},
    )

    snapshot = await probe.probe_once()

    assert snapshot["slowsrv"]["last_ok"] is False
    assert snapshot["slowsrv"]["last_reason"] == "timeout"
    assert snapshot["slowsrv"]["consecutive_fails"] == 1
    assert snapshot["slowsrv"]["total_fails"] == 1


@pytest.mark.asyncio
async def test_probe_exception_classified(fake_clock, monkeypatch):
    manager = _StubManager(
        {"broken": _StubSession(raises=ConnectionError("boom"))}
    )
    probe = _make_probe(manager, fake_clock)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"broken": {}},
    )

    snapshot = await probe.probe_once()

    assert snapshot["broken"]["last_ok"] is False
    assert snapshot["broken"]["last_reason"] == "exception"
    assert "boom" in snapshot["broken"]["last_error"]


@pytest.mark.asyncio
async def test_probe_empty_tools_classified_as_no_tools(fake_clock, monkeypatch):
    manager = _StubManager({"empty": _StubSession(tools=[])})
    probe = _make_probe(manager, fake_clock)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"empty": {}},
    )

    snapshot = await probe.probe_once()

    assert snapshot["empty"]["last_ok"] is False
    assert snapshot["empty"]["last_reason"] == "no_tools"


@pytest.mark.asyncio
async def test_consecutive_fails_resets_on_recovery(fake_clock, monkeypatch):
    session_state: dict[str, Any] = {"raises": RuntimeError("down")}

    class _Toggle:
        async def list_tools(self) -> Any:
            if session_state["raises"]:
                raise session_state["raises"]

            class _R:
                tools = [object()]

            return _R()

    manager = _StubManager({"flaky": _Toggle()})
    probe = _make_probe(manager, fake_clock)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"flaky": {}},
    )

    # 3 провала подряд.
    for _ in range(3):
        await probe.probe_once()
    snap_fail = probe.get_snapshot()
    assert snap_fail["flaky"]["consecutive_fails"] == 3
    assert snap_fail["flaky"]["total_fails"] == 3

    # Сервер восстановился → consecutive обнуляется, total сохраняется.
    session_state["raises"] = None
    await probe.probe_once()
    snap_ok = probe.get_snapshot()
    assert snap_ok["flaky"]["last_ok"] is True
    assert snap_ok["flaky"]["consecutive_fails"] == 0
    assert snap_ok["flaky"]["total_fails"] == 3


@pytest.mark.asyncio
async def test_snapshot_is_deep_copy(fake_clock, monkeypatch):
    manager = _StubManager({"telegram": _StubSession()})
    probe = _make_probe(manager, fake_clock)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"telegram": {}},
    )

    await probe.probe_once()
    snap = probe.get_snapshot()
    snap["telegram"]["last_ok"] = "MUTATED"

    # Внутренний state не должен пострадать.
    fresh = probe.get_snapshot()
    assert fresh["telegram"]["last_ok"] is True


@pytest.mark.asyncio
async def test_managed_server_without_session_marked_no_session(fake_clock, monkeypatch):
    manager = _StubManager({})  # никаких живых сессий
    probe = _make_probe(manager, fake_clock)
    monkeypatch.setattr(
        "src.core.mcp_registry.get_managed_mcp_servers",
        lambda: {"context7": {}, "firecrawl": {}},
    )

    snapshot = await probe.probe_once()

    assert snapshot["context7"]["last_ok"] is False
    assert snapshot["context7"]["last_reason"] == "no_session"
    assert snapshot["firecrawl"]["last_reason"] == "no_session"


def test_env_interval_handles_garbage(monkeypatch):
    monkeypatch.setenv("KRAB_MCP_PROBE_INTERVAL_SEC", "not_a_number")
    assert _env_interval() == 300.0

    monkeypatch.setenv("KRAB_MCP_PROBE_INTERVAL_SEC", "-5")
    assert _env_interval() == 300.0

    monkeypatch.setenv("KRAB_MCP_PROBE_INTERVAL_SEC", "")
    assert _env_interval() == 300.0

    monkeypatch.setenv("KRAB_MCP_PROBE_INTERVAL_SEC", "120")
    assert _env_interval() == 120.0
