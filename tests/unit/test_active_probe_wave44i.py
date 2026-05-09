# -*- coding: utf-8 -*-
"""Wave 44-I: active probe updates_subscriber via GetDialogs.

Bug fix для Wave 44-C false-positive: passive update_id probe в quiet
windows (3am-7am, 0 incoming traffic) показывал frozen update_id и
триггерил exit_78 без реального split-brain.

Wave 44-I (hybrid): passive first half → если frozen → active GetDialogs
probe → если probe OK = quiet but alive (return True, no false-positive);
если probe fails = real split-brain (return False через вторую passive
половину).
"""

from __future__ import annotations

import asyncio

import pytest


class _StubOwner:
    """Minimal duck-type для _probe_updates_flow_alive."""

    def __init__(self, update_id: int = 0, client: object | None = None) -> None:
        self._last_seen_update_id = update_id
        self.client = client


class _StubClient:
    """Minimal Pyrogram-like client с управляемым invoke."""

    def __init__(self, behaviour: str = "ok") -> None:
        # behaviour: "ok" / "timeout" / "error"
        self.behaviour = behaviour
        self.invoke_calls: list[object] = []

    async def invoke(self, query: object) -> object:
        self.invoke_calls.append(query)
        if self.behaviour == "ok":
            return []
        if self.behaviour == "timeout":
            await asyncio.sleep(60)  # будет cancelled wait_for'ом
            return []
        if self.behaviour == "rpc_error":
            raise RuntimeError("RPC_ERROR_SIMULATED")
        raise RuntimeError(f"unknown behaviour: {self.behaviour}")


# ---------------------------------------------------------------------------
# 1. _active_probe_updates_subscriber unit tests
# ---------------------------------------------------------------------------


class TestActiveProbeUpdatesSubscriber:
    @pytest.mark.asyncio
    async def test_active_probe_returns_true_on_success(self) -> None:
        from src.userbot.network_watchdog import _active_probe_updates_subscriber

        client = _StubClient(behaviour="ok")
        result = await _active_probe_updates_subscriber(client, timeout_sec=2.0)
        assert result is True
        assert len(client.invoke_calls) == 1

    @pytest.mark.asyncio
    async def test_active_probe_returns_false_on_timeout(self) -> None:
        from src.userbot.network_watchdog import _active_probe_updates_subscriber

        client = _StubClient(behaviour="timeout")
        result = await _active_probe_updates_subscriber(client, timeout_sec=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_active_probe_returns_false_on_exception(self) -> None:
        from src.userbot.network_watchdog import _active_probe_updates_subscriber

        client = _StubClient(behaviour="rpc_error")
        result = await _active_probe_updates_subscriber(client, timeout_sec=2.0)
        assert result is False


# ---------------------------------------------------------------------------
# 2. _probe_updates_flow_alive hybrid behaviour tests
# ---------------------------------------------------------------------------


class TestProbeUpdatesFlowHybrid:
    @pytest.mark.asyncio
    async def test_probe_flow_skips_active_when_flag_false(self) -> None:
        """active_probe_on_silence=False → frozen update_id даёт False, без invoke."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        client = _StubClient(behaviour="ok")
        owner = _StubOwner(update_id=100, client=client)

        result = await _probe_updates_flow_alive(
            owner, settle_sec=0.05, active_probe_on_silence=False
        )
        assert result is False
        assert client.invoke_calls == [], "не должно быть active probe при flag=False"

    @pytest.mark.asyncio
    async def test_probe_flow_returns_true_on_passive_movement(self) -> None:
        """update_id двинулся в первой половине → True без active probe."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        client = _StubClient(behaviour="ok")
        owner = _StubOwner(update_id=100, client=client)

        async def _bump() -> None:
            await asyncio.sleep(0.01)
            owner._last_seen_update_id = 150

        task = asyncio.create_task(_bump())
        result = await _probe_updates_flow_alive(owner, settle_sec=0.10)
        await task
        assert result is True
        assert client.invoke_calls == [], "active probe не нужен — passive увидел движение"

    @pytest.mark.asyncio
    async def test_probe_flow_active_probe_saves_quiet_window(self) -> None:
        """Frozen update_id + active probe OK → True (eliminates false-positive)."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        client = _StubClient(behaviour="ok")
        owner = _StubOwner(update_id=100, client=client)

        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is True, "quiet window must NOT trigger false-failure"
        assert len(client.invoke_calls) == 1, "active probe должен был быть вызван"

    @pytest.mark.asyncio
    async def test_probe_flow_active_probe_confirms_split_brain(self) -> None:
        """Frozen update_id + active probe FAIL → False (real split-brain)."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        client = _StubClient(behaviour="rpc_error")
        owner = _StubOwner(update_id=100, client=client)

        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is False, "active probe failed → split-brain должен подтвердиться"
        assert len(client.invoke_calls) == 1
