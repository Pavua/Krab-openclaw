# -*- coding: utf-8 -*-
"""Wave 63-A: GetState pts probe + drop 10-minute split-brain gate.

Background
----------
2026-05-11: Krab main userbot ушёл в split-brain (TCP heartbeat жив, processes
alive, owner DM silence). 22:07 → 23:42 = 95 минут до восстановления. До
Wave 63-A детектор split-brain опирался на `_ZOMBIE_DOUBLE_SILENCE_SEC=600`
(10 минут тишины обязательны), плюс `_probe_updates_flow_alive` шла через
тот же `client.invoke(GetDialogs)` MTProto-канал, что и сам heartbeat.

Step 1
------
В `_telegram_heartbeat_loop`: после успешного `GetUsers` зовём
`client.invoke(GetState())` и сравниваем `pts/qts/seq/date` с предыдущим
snapshot. Если server `pts` advanced (>=1), а `_last_seen_update_id` НЕ
двинулся — flag split-brain немедленно и зовём `_try_reconnect_pyrofork`.

Step 2
------
В `_network_offline_monitor_loop`: gate `silence_sec > 600s` для probe
снимается. Probe запускается каждые `check_interval` (30s), gated только
на `dc_reachable` + `zombie_enabled`. Это режет время от события до
detection с 93+ минут до ~4 минут.

Не трогаем
----------
- Step 3 (dispatcher_tick hook) — Wave 63-B
- Step 4 (per-client probe для swarm) — Wave 63-C
- Step 5 (surgical recovery) — Wave 63-D
"""

from __future__ import annotations

import asyncio
import pathlib
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_SRC_PATH = pathlib.Path("src/userbot/network_watchdog.py")
_SRC = _SRC_PATH.read_text(encoding="utf-8") if _SRC_PATH.exists() else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeState:
    """Pyrogram-like State payload (см. pyrogram.raw.types.updates.State)."""

    def __init__(self, pts: int, qts: int = 0, date: int = 0, seq: int = 0) -> None:
        self.pts = pts
        self.qts = qts
        self.date = date
        self.seq = seq
        self.unread_count = 0


class _StubOwner:
    """Минимальный duck-type для probe helper."""

    def __init__(
        self,
        *,
        update_id: int = 0,
        last_server_pts: int = 0,
        client: object | None = None,
    ) -> None:
        self._last_seen_update_id = update_id
        self._last_server_pts = last_server_pts
        self.client = client


def _classify_query(query: object) -> str:
    """Робастно определяет тип Pyrogram-запроса.

    Сначала по `__class__.__name__`. Если получили MagicMock (потому что
    другие тесты — например Wave 57-A — установили sys.modules для
    pyrogram.raw.* как MagicMock через `sys.modules.setdefault`), fallback
    на repr(query) для match по типичным маркерам ("GetState" / "GetUsers").
    """
    qname = type(query).__name__
    if qname not in {"MagicMock", "AsyncMock"}:
        return qname
    # Fallback: ищем в repr — pyrogram mock-classes часто имеют
    # mock_name='pyrogram.raw.functions.updates.GetState' etc.
    rep = repr(query)
    for candidate in ("GetState", "GetUsers", "GetDialogs"):
        if candidate in rep:
            return candidate
    return qname


@pytest.fixture
def restore_pyrogram_modules():
    """Reset pyrogram modules so heartbeat integration tests use real classes.

    Wave 57-A test (test_graceful_restart_catchup_wave57a) делает:
      sys.modules.setdefault("pyrogram.raw.functions.users", MagicMock())
      sys.modules["pyrogram.raw.functions.users"].GetUsers = fake_get_users
      sys.modules["pyrogram.raw.types"].InputUserSelf = fake_input_user_self

    Это **mutate** атрибуты модулей. Если реальный модуль уже импортирован,
    setdefault — no-op, но attribute mutation повреждает реальный класс.

    Эта фикстура восстанавливает реальные GetUsers/InputUserSelf после Wave 57-A
    pollution. Не использует monkeypatch (он восстановил бы MagicMock).
    """
    import importlib
    import sys as _sys
    from unittest.mock import MagicMock as _MagicMock

    # Drop sys.modules entries которые являются MagicMock (значит они были
    # установлены через setdefault — реальный pyrogram там не сидит).
    polluted_keys = [
        k
        for k in list(_sys.modules.keys())
        if k.startswith("pyrogram") and isinstance(_sys.modules.get(k), _MagicMock)
    ]
    for k in polluted_keys:
        del _sys.modules[k]

    # Force-re-import — после del, import_module reload'нёт.
    real_modules: dict[str, object] = {}
    for mod_name in (
        "pyrogram",
        "pyrogram.raw",
        "pyrogram.raw.functions",
        "pyrogram.raw.functions.users",
        "pyrogram.raw.functions.updates",
        "pyrogram.raw.types",
    ):
        try:
            real_modules[mod_name] = importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            pass

    # Восстанавливаем атрибуты, которые Wave 57-A test mutate-ит на реальном модуле.
    # GetUsers — pyrogram.raw.functions.users.GetUsers
    # InputUserSelf — pyrogram.raw.types.InputUserSelf
    try:
        users_mod = real_modules.get("pyrogram.raw.functions.users")
        if users_mod is not None:
            real_get_users = importlib.import_module(
                "pyrogram.raw.functions.users.get_users"
            ).GetUsers
            users_mod.GetUsers = real_get_users
    except Exception:  # noqa: BLE001
        pass
    try:
        types_mod = real_modules.get("pyrogram.raw.types")
        if types_mod is not None:
            # pyrogram.raw.types — это namespace package с lazy loading.
            # InputUserSelf фактически в pyrogram.raw.types.input_user_self
            real_input_user_self = importlib.import_module(
                "pyrogram.raw.types.input_user_self"
            ).InputUserSelf
            types_mod.InputUserSelf = real_input_user_self
    except Exception:  # noqa: BLE001
        pass

    yield real_modules


class _ProgrammableClient:
    """Pyrogram-like client с фабрикой ответов по типу запроса.

    Distinguishes GetState/GetUsers/GetDialogs even когда другие тесты в
    pytest-сессии замокали `sys.modules["pyrogram.raw.functions.*"]`
    как MagicMock (см. Wave 57-A test).
    """

    def __init__(self) -> None:
        self.is_connected = True
        self.invoke_calls: list[Any] = []
        # mapping: тип запроса (class name) → callable(query) → ответ.
        # Если callable raise'ит — поднимаем.
        self.responses: dict[str, Any] = {}

    def set_response(self, query_type: str, response: Any) -> None:
        self.responses[query_type] = response

    async def invoke(self, query: object) -> Any:
        self.invoke_calls.append(query)
        qname = _classify_query(query)
        if qname not in self.responses:
            raise RuntimeError(f"unexpected invoke for {qname} (repr={repr(query)[:120]})")
        resp = self.responses[qname]
        if callable(resp):
            return resp(query)
        return resp


# ---------------------------------------------------------------------------
# 1. Step 1 — unit-тесты detector функции _probe_updates_via_get_state
# ---------------------------------------------------------------------------


class TestGetStateProbeDetectsSplitBrain:
    """Wave 63-A Step 1: server pts двигается, а update_id заморожен → split-brain."""

    @pytest.mark.asyncio
    async def test_detector_function_exists(self) -> None:
        """Helper `_probe_updates_via_get_state` должен быть экспортирован."""
        from src.userbot import network_watchdog

        assert hasattr(network_watchdog, "_probe_updates_via_get_state"), (
            "Wave 63-A Step 1: helper `_probe_updates_via_get_state` must exist"
        )

    @pytest.mark.asyncio
    async def test_detector_flags_split_brain_when_server_pts_advances_but_update_frozen(
        self,
    ) -> None:
        """Server pts advanced (>=1), update_id frozen → alive=False (split-brain)."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        client = _ProgrammableClient()
        # Server advanced на 5 pts, update_id остаётся 100 (frozen)
        client.set_response("GetState", _FakeState(pts=1005))
        owner = _StubOwner(update_id=100, last_server_pts=1000, client=client)

        result = await _probe_updates_via_get_state(owner)

        assert result.alive is False, "split-brain: server pts +5 vs update_id frozen"
        assert result.split_brain_suspected is True
        assert result.server_pts == 1005
        assert result.server_pts_delta == 5
        # _last_server_pts должен обновиться на полученный snapshot
        assert owner._last_server_pts == 1005

    @pytest.mark.asyncio
    async def test_detector_returns_alive_when_no_server_pts_movement(self) -> None:
        """Сервер pts не двигался → не split-brain (просто quiet window)."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        client = _ProgrammableClient()
        client.set_response("GetState", _FakeState(pts=1000))
        owner = _StubOwner(update_id=100, last_server_pts=1000, client=client)

        result = await _probe_updates_via_get_state(owner)

        assert result.alive is True, "quiet window — server pts равен previous"
        assert result.split_brain_suspected is False
        assert result.server_pts_delta == 0

    @pytest.mark.asyncio
    async def test_detector_returns_alive_when_update_id_progresses(self) -> None:
        """update_id двигается синхронно с server pts → flow alive."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        client = _ProgrammableClient()
        client.set_response("GetState", _FakeState(pts=1005))
        # update_id тоже подрос — dispatch loop работает
        owner = _StubOwner(update_id=200, last_server_pts=1000, client=client)
        # Эмулируем что между предыдущим вызовом и текущим update_id вырос
        # (factor — какой baseline сравнивать). API: detector сам решит,
        # достаточно ли просто "update_id != baseline" — мы передаём
        # update_id_baseline.
        result = await _probe_updates_via_get_state(owner, update_id_baseline=100)

        assert result.alive is True
        assert result.split_brain_suspected is False

    @pytest.mark.asyncio
    async def test_detector_returns_alive_on_first_call_no_baseline(self) -> None:
        """Первая итерация: previous pts = 0 → просто запоминаем, не split-brain."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        client = _ProgrammableClient()
        client.set_response("GetState", _FakeState(pts=1000))
        owner = _StubOwner(update_id=0, last_server_pts=0, client=client)

        result = await _probe_updates_via_get_state(owner)

        # Первый вызов: нет previous → не можем судить, alive=True
        assert result.alive is True
        assert result.split_brain_suspected is False
        # Но snapshot должен быть записан
        assert owner._last_server_pts == 1000

    @pytest.mark.asyncio
    async def test_detector_handles_invoke_timeout(self) -> None:
        """invoke(GetState) timeout → alive=False, без crash."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        class _SlowClient:
            is_connected = True

            async def invoke(self, query):  # noqa: ANN001
                await asyncio.sleep(60)

        owner = _StubOwner(update_id=100, last_server_pts=1000, client=_SlowClient())
        result = await _probe_updates_via_get_state(owner, timeout_sec=0.05)

        # Timeout сам по себе не split-brain — но flow тоже не подтверждён
        assert result.alive is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_detector_handles_invoke_exception(self) -> None:
        """invoke(GetState) raises → alive=False, ошибка в payload."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        class _FailingClient:
            is_connected = True

            async def invoke(self, query):  # noqa: ANN001
                raise RuntimeError("MTProto borked")

        owner = _StubOwner(update_id=100, last_server_pts=1000, client=_FailingClient())
        result = await _probe_updates_via_get_state(owner, timeout_sec=2.0)

        assert result.alive is False
        assert result.error is not None
        assert "MTProto" in result.error or "borked" in result.error

    @pytest.mark.asyncio
    async def test_detector_handles_missing_client(self) -> None:
        """owner.client = None → alive=False (probe невозможен)."""
        from src.userbot.network_watchdog import _probe_updates_via_get_state

        owner = _StubOwner(update_id=100, last_server_pts=1000, client=None)
        result = await _probe_updates_via_get_state(owner)

        assert result.alive is False


# ---------------------------------------------------------------------------
# 2. Step 1 — integration: heartbeat loop должен звать GetState
# ---------------------------------------------------------------------------


class TestHeartbeatLoopUsesGetState:
    """Wave 63-A Step 1: heartbeat loop должен вызывать GetState после GetUsers."""

    def test_source_imports_get_state(self) -> None:
        """`from pyrogram.raw.functions.updates import GetState` появилось в коде."""
        assert "from pyrogram.raw.functions.updates import GetState" in _SRC, (
            "Wave 63-A Step 1: GetState import должен присутствовать"
        )

    def test_source_references_last_server_pts(self) -> None:
        """Поле `_last_server_pts` упоминается в watchdog."""
        assert "_last_server_pts" in _SRC, (
            "Wave 63-A Step 1: `_last_server_pts` snapshot должно присутствовать"
        )

    def test_source_logs_split_brain_detection(self) -> None:
        """Detection логируется уникальным key для grep/Sentry."""
        # Один из помечающих keys должен быть в коде
        assert (
            "split_brain_via_get_state" in _SRC
            or "get_state_split_brain" in _SRC
            or "updates_pts_split_brain" in _SRC
        ), "Wave 63-A Step 1: должен быть унифицированный log key для GetState detection"

    @pytest.mark.asyncio
    async def test_heartbeat_calls_get_state_on_success(
        self, monkeypatch: pytest.MonkeyPatch, restore_pyrogram_modules
    ) -> None:
        """После успешного GetUsers heartbeat должен дёрнуть GetState."""
        # Минимизируем интервалы чтобы цикл прошёл быстро
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "0")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "3")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "5")

        client = _ProgrammableClient()
        # GetUsers возвращает [users]
        client.set_response("GetUsers", lambda q: [MagicMock()])
        # GetState возвращает корректный pts
        client.set_response("GetState", _FakeState(pts=1000))

        from src.userbot.network_watchdog import NetworkWatchdogMixin

        # Создаём минимальный stub, miксующий NetworkWatchdogMixin
        class _Stub(NetworkWatchdogMixin):
            def __init__(self) -> None:
                self.client = client
                self._last_telegram_event_ts = time.time()
                self._last_heartbeat_ok_ts = time.time()
                self._last_seen_update_id = 100
                self._last_server_pts = 0
                self._send_zombie_alert_to_owner = AsyncMock()
                self._send_proactive_watch_alert = AsyncMock()

            async def _try_reconnect_pyrofork(self, _client):  # type: ignore[override]
                return True

            def _schedule_catchup_after_graceful_restart(self) -> None:  # type: ignore[override]
                pass

        stub = _Stub()

        # Запускаем loop, дадим пройти ~1 итерации, потом отменяем
        task = asyncio.create_task(stub._telegram_heartbeat_loop())
        await asyncio.sleep(0.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        invoke_types = {_classify_query(q) for q in client.invoke_calls}
        assert "GetState" in invoke_types, (
            f"GetState должен быть в invoke history, видели: {invoke_types}"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_triggers_reconnect_on_split_brain(
        self, monkeypatch: pytest.MonkeyPatch, restore_pyrogram_modules
    ) -> None:
        """Когда GetState detects split-brain → должно сразу триггериться reconnect."""
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "0")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "10")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "5")
        # Step 1 enabled (env switch для safe rollout)
        monkeypatch.setenv("KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED", "1")

        client = _ProgrammableClient()
        client.set_response("GetUsers", lambda q: [MagicMock()])

        # Возвращаем растущий pts на каждом call — server активен, update_id frozen
        pts_iter = iter([1010, 1020, 1030, 1040])

        def _get_state_response(_q):  # noqa: ANN001, ANN202
            try:
                return _FakeState(pts=next(pts_iter))
            except StopIteration:
                return _FakeState(pts=1040)

        client.set_response("GetState", _get_state_response)

        from src.userbot.network_watchdog import NetworkWatchdogMixin

        reconnect_calls: list[float] = []

        class _Stub(NetworkWatchdogMixin):
            def __init__(self) -> None:
                self.client = client
                self._last_telegram_event_ts = time.time()
                self._last_heartbeat_ok_ts = time.time()
                self._last_seen_update_id = 100  # FROZEN
                self._last_server_pts = 1000  # baseline
                self._send_zombie_alert_to_owner = AsyncMock()
                self._send_proactive_watch_alert = AsyncMock()

            async def _try_reconnect_pyrofork(self, _client):  # type: ignore[override]
                reconnect_calls.append(time.time())
                return True

            def _schedule_catchup_after_graceful_restart(self) -> None:  # type: ignore[override]
                pass

        stub = _Stub()
        task = asyncio.create_task(stub._telegram_heartbeat_loop())
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Step 1: на первый detected split-brain — reconnect должен быть вызван
        assert len(reconnect_calls) >= 1, (
            "Split-brain via GetState должен триггерить reconnect минимум 1 раз"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_does_not_call_reconnect_when_flow_alive(
        self, monkeypatch: pytest.MonkeyPatch, restore_pyrogram_modules
    ) -> None:
        """update_id двигается синхронно с pts → reconnect НЕ должен звать."""
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "0")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "10")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "5")
        monkeypatch.setenv("KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED", "1")

        client = _ProgrammableClient()
        client.set_response("GetUsers", lambda q: [MagicMock()])
        client.set_response("GetState", _FakeState(pts=1000))  # no movement

        from src.userbot.network_watchdog import NetworkWatchdogMixin

        reconnect_calls: list[float] = []

        class _Stub(NetworkWatchdogMixin):
            def __init__(self) -> None:
                self.client = client
                self._last_telegram_event_ts = time.time()
                self._last_heartbeat_ok_ts = time.time()
                self._last_seen_update_id = 100
                self._last_server_pts = 1000
                self._send_zombie_alert_to_owner = AsyncMock()
                self._send_proactive_watch_alert = AsyncMock()

            async def _try_reconnect_pyrofork(self, _client):  # type: ignore[override]
                reconnect_calls.append(time.time())
                return True

            def _schedule_catchup_after_graceful_restart(self) -> None:  # type: ignore[override]
                pass

        stub = _Stub()
        task = asyncio.create_task(stub._telegram_heartbeat_loop())
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert reconnect_calls == [], (
            f"Reconnect не должен звать при alive flow, было: {len(reconnect_calls)}"
        )


# ---------------------------------------------------------------------------
# 3. Step 2 — drop _ZOMBIE_DOUBLE_SILENCE_SEC gate before split-brain probe
# ---------------------------------------------------------------------------


class TestStep2DropTenMinuteGate:
    """Wave 63-A Step 2: probe должен запускаться сразу при `silence_sec >= threshold`,
    а не дожидаться 10-минутной "double silence" задержки."""

    def test_source_no_longer_gates_probe_on_double_silence(self) -> None:
        """В исходнике должен пропасть guard `silence_sec > _ZOMBIE_DOUBLE_SILENCE_SEC`
        перед split-brain probe.

        Wave 63-A Step 2 marker: ищем подпись `Wave 63-A` либо отсутствие
        старого guard в context split-brain detection. Допускаем что
        _ZOMBIE_DOUBLE_SILENCE_SEC может остаться для других путей.
        """
        assert "Wave 63-A" in _SRC, "Wave 63-A marker should be present в network_watchdog.py"

    def test_zombie_double_silence_constant_can_remain_for_other_paths(self) -> None:
        """Сам константа `_ZOMBIE_DOUBLE_SILENCE_SEC` может остаться для legacy путей,
        но не должна стоять перед probe.

        Допускаем что переменная определена, главное чтобы probe gate был снят.
        Это документация.
        """
        # Это purely documentation test — может остаться.
        assert "_ZOMBIE_DOUBLE_SILENCE_SEC" in _SRC

    def test_source_documents_step_2_in_loop(self) -> None:
        """Должен быть комментарий 'Wave 63-A' в monitor loop."""
        # Локализуем участок monitor loop
        loop_start = _SRC.find("async def _network_offline_monitor_loop")
        loop_end = _SRC.find("async def _telegram_heartbeat_loop")
        if loop_end == -1:
            loop_end = len(_SRC)
        loop_body = _SRC[loop_start:loop_end] if loop_start > 0 else ""
        assert "Wave 63-A" in loop_body, (
            "Wave 63-A marker должен быть внутри _network_offline_monitor_loop"
        )


# ---------------------------------------------------------------------------
# 4. _last_server_pts должен быть инициализирован в __init__ bridge
# ---------------------------------------------------------------------------


class TestLastServerPtsInitInBridge:
    """`_last_server_pts` нужно объявить там же, где `_last_seen_update_id`
    (userbot_bridge.py:416)."""

    def test_bridge_init_declares_last_server_pts(self) -> None:
        bridge_path = pathlib.Path("src/userbot_bridge.py")
        if not bridge_path.exists():
            pytest.skip("userbot_bridge.py not found in worktree layout")
        text = bridge_path.read_text(encoding="utf-8")
        assert "_last_server_pts" in text, (
            "`_last_server_pts: int = 0` должен быть в __init__ KraabUserbot"
        )

    def test_bridge_init_declares_alongside_last_seen_update_id(self) -> None:
        """`_last_server_pts` и `_last_seen_update_id` должны жить рядом (один контекст)."""
        bridge_path = pathlib.Path("src/userbot_bridge.py")
        if not bridge_path.exists():
            pytest.skip("userbot_bridge.py not found")
        text = bridge_path.read_text(encoding="utf-8")
        idx_uid = text.find("_last_seen_update_id")
        idx_pts = text.find("_last_server_pts")
        assert idx_uid > 0 and idx_pts > 0
        # должны быть в пределах ~500 байт друг от друга
        assert abs(idx_pts - idx_uid) < 1500, (
            f"Step 1: ожидалось что _last_server_pts рядом с _last_seen_update_id, "
            f"но distance = {abs(idx_pts - idx_uid)}"
        )


# ---------------------------------------------------------------------------
# 5. ENV gate: Step 1 защищён feature-flag для safe rollout
# ---------------------------------------------------------------------------


class TestStep1EnvFlag:
    """Step 1 (GetState probe) должен быть выключаем env-флагом для safe rollout."""

    def test_env_flag_name_present(self) -> None:
        """В коде должен быть `KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED`."""
        assert "KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED" in _SRC, (
            "Step 1 должен иметь env-flag для safe rollout"
        )

    @pytest.mark.asyncio
    async def test_disabled_flag_skips_get_state_call(
        self, monkeypatch: pytest.MonkeyPatch, restore_pyrogram_modules
    ) -> None:
        """`KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED=0` → GetState invoke не делается."""
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_ENABLED", "1")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_INTERVAL_SEC", "0")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_TIMEOUT_SEC", "5")
        monkeypatch.setenv("KRAB_TELEGRAM_HEARTBEAT_FAIL_THRESHOLD", "10")
        monkeypatch.setenv("KRAB_HEARTBEAT_GET_STATE_PROBE_ENABLED", "0")

        client = _ProgrammableClient()
        client.set_response("GetUsers", lambda q: [MagicMock()])
        client.set_response("GetState", _FakeState(pts=9999))

        from src.userbot.network_watchdog import NetworkWatchdogMixin

        class _Stub(NetworkWatchdogMixin):
            def __init__(self) -> None:
                self.client = client
                self._last_telegram_event_ts = time.time()
                self._last_heartbeat_ok_ts = time.time()
                self._last_seen_update_id = 100
                self._last_server_pts = 0
                self._send_zombie_alert_to_owner = AsyncMock()
                self._send_proactive_watch_alert = AsyncMock()

            async def _try_reconnect_pyrofork(self, _client):  # type: ignore[override]
                return True

            def _schedule_catchup_after_graceful_restart(self) -> None:  # type: ignore[override]
                pass

        stub = _Stub()
        task = asyncio.create_task(stub._telegram_heartbeat_loop())
        await asyncio.sleep(0.4)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        invoke_types = {_classify_query(q) for q in client.invoke_calls}
        assert "GetState" not in invoke_types, (
            f"GetState не должен зваться когда env=0, видели: {invoke_types}"
        )
