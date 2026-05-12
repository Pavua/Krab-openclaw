# -*- coding: utf-8 -*-
"""Wave 63-B: per-client GetState probe для swarm Telegram-сессий.

Wave 63-A решил split-brain detection для main kraab.session за счёт
heartbeat-loop, который вызывает `updates.GetState()` и сравнивает server
`pts` с `_last_seen_update_id`. Swarm team clients (`traders`/`coders`/
`analysts`/`creative`) живут в собственных Pyrogram Client instances и до
сих пор не имели собственного probe — они могли висеть в split-brain
часами незаметно.

Wave 63-B: каждый swarm-клиент получает периодический `_per_client_probe_loop`,
который раз в `KRAB_SWARM_PROBE_INTERVAL_SEC` (default 240s) дёргает
`GetState()`, сохраняет snapshot `{pts, qts, seq, date}` и если все 4 поля
не двигались два интервала подряд при connected client → log
`swarm_split_brain_detected` + graceful reconnect ЭТОГО клиента (main kraab
не трогаем).

Cleanup: `_stop_swarm_team_clients` отменяет все probe-задачи через
stored task handles (`_swarm_probe_tasks`).

Env gate: `KRAB_SWARM_PROBE_ENABLED=1` (default ON).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.userbot.swarm_team_clients import (
    SwarmTeamClientsMixin,
    _swarm_probe_enabled,
)


class _FakeState:
    """pyrogram.raw.types.updates.State-like payload."""

    def __init__(
        self,
        *,
        pts: int = 0,
        qts: int = 0,
        seq: int = 0,
        date: int = 0,
    ) -> None:
        self.pts = pts
        self.qts = qts
        self.seq = seq
        self.date = date
        self.unread_count = 0


class _ProgrammableSwarmClient:
    """Минимальный Pyrogram-like клиент для swarm probe тестов."""

    def __init__(self) -> None:
        self.is_connected = True
        self.invoke_calls: list[Any] = []
        self.stop_calls: int = 0
        self.start_calls: int = 0
        # Очередь ответов на GetState — выдаём по одному, последний repeating.
        self._state_queue: list[_FakeState] = []
        self._raise_on_invoke: Exception | None = None

    def queue_states(self, states: list[_FakeState]) -> None:
        self._state_queue = list(states)

    def fail_invoke_with(self, exc: Exception) -> None:
        self._raise_on_invoke = exc

    async def invoke(self, query: object) -> Any:
        self.invoke_calls.append(query)
        if self._raise_on_invoke is not None:
            raise self._raise_on_invoke
        if not self._state_queue:
            return _FakeState(pts=0)
        if len(self._state_queue) == 1:
            return self._state_queue[0]
        return self._state_queue.pop(0)

    async def stop(self, block: bool = True) -> None:  # noqa: ARG002
        self.stop_calls += 1
        self.is_connected = False

    async def start(self) -> None:
        self.start_calls += 1
        self.is_connected = True


class _Stub(SwarmTeamClientsMixin):
    """Чистый instance с поведениями mixin, без других mixins KraabUserbot."""

    def __init__(self) -> None:
        self._swarm_team_clients = {}
        self._last_swarm_pts = {}
        self._swarm_probe_tasks = {}


@pytest.fixture(autouse=True)
def _fast_probe_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Уменьшаем интервал probe чтобы тесты не висели — модуль читает env
    один раз при import, поэтому monkeypatch применим через прямой override
    констант. Здесь — общий маленький interval (но >= 30 из-за max())."""
    # Min 30 на check внутри модуля — заменим напрямую через monkeypatch
    # модульного значения, чтобы не зависеть от env import-time гонки.
    import src.userbot.swarm_team_clients as mod

    monkeypatch.setattr(mod, "_SWARM_PROBE_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(mod, "_SWARM_PROBE_TIMEOUT_SEC", 1.0)


class TestSwarmProbeEnvFlag:
    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KRAB_SWARM_PROBE_ENABLED", raising=False)
        assert _swarm_probe_enabled() is True

    def test_disabled_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRAB_SWARM_PROBE_ENABLED", "0")
        assert _swarm_probe_enabled() is False


class TestPerClientProbeLoop:
    @pytest.mark.asyncio
    async def test_normal_movement_no_detection(self) -> None:
        """Если pts двигается между probe — никакого split-brain log/reconnect."""
        stub = _Stub()
        client = _ProgrammableSwarmClient()
        # Каждый probe возвращает новый pts → движение есть
        client.queue_states(
            [
                _FakeState(pts=100, qts=0, seq=10, date=1700000000),
                _FakeState(pts=110, qts=0, seq=11, date=1700000001),
                _FakeState(pts=120, qts=0, seq=12, date=1700000002),
                _FakeState(pts=130, qts=0, seq=13, date=1700000003),
            ]
        )

        task = asyncio.create_task(stub._per_client_probe_loop("traders", client))
        await asyncio.sleep(0.3)  # >= 4 интервала
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client.stop_calls == 0, "Reconnect не должен дёргаться при движении"
        assert client.start_calls == 0
        assert stub._last_swarm_pts.get("traders") is not None

    @pytest.mark.asyncio
    async def test_stuck_pts_detects_split_brain_and_reconnects(self) -> None:
        """Все 4 поля не двигаются 2 интервала → log + reconnect."""
        stub = _Stub()
        client = _ProgrammableSwarmClient()
        frozen = _FakeState(pts=500, qts=0, seq=5, date=1700000500)
        # Любой invoke возвращает один и тот же frozen state
        client.queue_states([frozen])

        task = asyncio.create_task(stub._per_client_probe_loop("coders", client))
        # Нужно: baseline (1) + 2 stagnant интервала = 3 probe минимум
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client.stop_calls >= 1, "Должен быть как минимум 1 stop() для reconnect"
        assert client.start_calls >= 1, "Должен быть как минимум 1 start() для reconnect"

    @pytest.mark.asyncio
    async def test_one_field_moves_no_detection(self) -> None:
        """Достаточно движения одного из 4 полей (например date) → не split-brain."""
        stub = _Stub()
        client = _ProgrammableSwarmClient()
        # pts/qts/seq заморожены, date растёт — это нормальный server tick
        client.queue_states(
            [
                _FakeState(pts=10, qts=0, seq=1, date=1700000000),
                _FakeState(pts=10, qts=0, seq=1, date=1700000001),
                _FakeState(pts=10, qts=0, seq=1, date=1700000002),
                _FakeState(pts=10, qts=0, seq=1, date=1700000003),
            ]
        )

        task = asyncio.create_task(stub._per_client_probe_loop("analysts", client))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client.stop_calls == 0
        assert client.start_calls == 0

    @pytest.mark.asyncio
    async def test_disconnected_client_skipped(self) -> None:
        """Если client.is_connected=False — invoke не делается, reconnect не зовётся."""
        stub = _Stub()
        client = _ProgrammableSwarmClient()
        client.is_connected = False

        task = asyncio.create_task(stub._per_client_probe_loop("creative", client))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert client.invoke_calls == []
        assert client.stop_calls == 0
        assert client.start_calls == 0

    @pytest.mark.asyncio
    async def test_invoke_exception_does_not_crash_loop(self) -> None:
        """Транзиентная ошибка invoke логируется warning, loop продолжает работу."""
        stub = _Stub()
        client = _ProgrammableSwarmClient()
        client.fail_invoke_with(RuntimeError("MTProto wobble"))

        task = asyncio.create_task(stub._per_client_probe_loop("traders", client))
        await asyncio.sleep(0.2)
        assert not task.done(), "Loop должен переживать invoke exception"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class TestStartupAndCleanup:
    @pytest.mark.asyncio
    async def test_init_starts_probe_task_per_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_init_swarm_team_clients создаёт probe task на каждый team."""
        stub = _Stub()
        clients = {
            "traders": _ProgrammableSwarmClient(),
            "coders": _ProgrammableSwarmClient(),
        }

        async def fake_start(self):  # noqa: ANN001
            return clients

        # Патчим method на bound уровне (через type) чтобы self.* работал
        monkeypatch.setattr(
            type(stub),
            "_start_swarm_team_clients",
            fake_start,
        )
        # Заглушаем bind_team_client и register_team_message_handler — у них
        # side-effects в swarm_channels / openclaw_client, не нужны тут.
        import src.userbot.swarm_team_clients as mod

        monkeypatch.setattr(mod.swarm_channels, "bind_team_client", MagicMock())
        # register_team_message_handler import-ится lazy внутри метода —
        # подменяем модуль до вызова.
        listener_mod = MagicMock()
        listener_mod.register_team_message_handler = MagicMock()
        monkeypatch.setitem(
            __import__("sys").modules,
            "src.core.swarm_team_listener",
            listener_mod,
        )

        monkeypatch.setenv("KRAB_SWARM_PROBE_ENABLED", "1")

        await stub._init_swarm_team_clients()

        assert set(stub._swarm_probe_tasks.keys()) == {"traders", "coders"}
        for task in stub._swarm_probe_tasks.values():
            assert isinstance(task, asyncio.Task)
            assert not task.done()

        # cleanup
        await stub._stop_swarm_team_clients()

    @pytest.mark.asyncio
    async def test_stop_cancels_probe_tasks(self) -> None:
        """_stop_swarm_team_clients отменяет probe tasks и очищает dict."""
        stub = _Stub()
        # Создаём pending tasks вручную — имитируем что init их завёл
        client = _ProgrammableSwarmClient()
        stub._swarm_team_clients = {"traders": client}

        async def _idle() -> None:
            while True:
                await asyncio.sleep(0.05)

        t1 = asyncio.create_task(_idle(), name="swarm_probe_traders")
        stub._swarm_probe_tasks = {"traders": t1}

        await stub._stop_swarm_team_clients()

        assert t1.cancelled() or t1.done(), "Probe task должен быть cancelled"
        assert stub._swarm_probe_tasks == {}

    @pytest.mark.asyncio
    async def test_init_skips_probe_when_env_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KRAB_SWARM_PROBE_ENABLED=0 → probe tasks не создаются."""
        stub = _Stub()
        clients = {"traders": _ProgrammableSwarmClient()}

        async def fake_start(self):  # noqa: ANN001
            return clients

        monkeypatch.setattr(type(stub), "_start_swarm_team_clients", fake_start)
        import src.userbot.swarm_team_clients as mod

        monkeypatch.setattr(mod.swarm_channels, "bind_team_client", MagicMock())
        listener_mod = MagicMock()
        listener_mod.register_team_message_handler = MagicMock()
        monkeypatch.setitem(
            __import__("sys").modules,
            "src.core.swarm_team_listener",
            listener_mod,
        )
        monkeypatch.setenv("KRAB_SWARM_PROBE_ENABLED", "0")

        await stub._init_swarm_team_clients()

        assert stub._swarm_probe_tasks == {}

        await stub._stop_swarm_team_clients()
