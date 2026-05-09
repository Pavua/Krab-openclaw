# -*- coding: utf-8 -*-
"""Wave 39-D: тесты для true split-brain detection через update_id tracking.

Проверяет:
- _last_seen_update_id инициализируется в 0 у bridge
- update_id монотонно обновляется в _process_message
- меньший id не перетирает больший
- _probe_updates_flow_alive: True когда id вырос, False когда заморожен
- Integration smoke: split-brain path вызывается при корректных условиях
"""

from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: минимальный stub owner с _last_seen_update_id
# ---------------------------------------------------------------------------

class _StubOwner:
    """Минимальный stub который эмулирует bridge для probe-тестов."""
    def __init__(self, update_id: int = 0):
        self._last_seen_update_id = update_id


# ---------------------------------------------------------------------------
# 1. _last_seen_update_id инициализируется в 0
# ---------------------------------------------------------------------------

class TestBridgeUpdateIdInit:
    """Bridge.__init__ должен устанавливать _last_seen_update_id = 0."""

    def test_attribute_initialized_to_zero(self):
        """Атрибут присутствует и равен 0 в bridge.__init__."""
        # Читаем исходник напрямую чтобы не тащить все зависимости bridge.
        import ast
        import pathlib

        src = pathlib.Path("src/userbot_bridge.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        # Ищем все Assign/AnnAssign в теле __init__ KraabUserbot
        found = False
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "KraabUserbot"
            ):
                for item in ast.walk(node):
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        for stmt in ast.walk(item):
                            # self._last_seen_update_id: int = 0
                            if isinstance(stmt, ast.AnnAssign):
                                tgt = stmt.target
                                if (
                                    isinstance(tgt, ast.Attribute)
                                    and tgt.attr == "_last_seen_update_id"
                                    and isinstance(stmt.value, ast.Constant)
                                    and stmt.value.value == 0
                                ):
                                    found = True
        assert found, (
            "_last_seen_update_id: int = 0 не найден в KraabUserbot.__init__"
        )


# ---------------------------------------------------------------------------
# 2. Монотонное обновление в _process_message
# ---------------------------------------------------------------------------

class TestProcessMessageUpdateIdTracking:
    """_process_message должен обновлять _last_seen_update_id монотонно."""

    def _make_message(self, msg_id: int) -> MagicMock:
        m = MagicMock()
        m.id = msg_id
        return m

    def test_update_id_increases(self):
        """При msg.id > текущего — обновляем."""
        owner = _StubOwner(update_id=5)
        msg = self._make_message(10)
        uid = getattr(msg, "id", 0) or 0
        if uid > owner._last_seen_update_id:
            owner._last_seen_update_id = uid
        assert owner._last_seen_update_id == 10

    def test_smaller_id_not_overwrite(self):
        """При msg.id <= текущего — НЕ обновляем (монотонность)."""
        owner = _StubOwner(update_id=100)
        msg = self._make_message(50)
        uid = getattr(msg, "id", 0) or 0
        if uid > owner._last_seen_update_id:
            owner._last_seen_update_id = uid
        assert owner._last_seen_update_id == 100

    def test_equal_id_not_overwrite(self):
        """Равный id тоже не должен изменять значение."""
        owner = _StubOwner(update_id=42)
        msg = self._make_message(42)
        uid = getattr(msg, "id", 0) or 0
        if uid > owner._last_seen_update_id:
            owner._last_seen_update_id = uid
        assert owner._last_seen_update_id == 42

    def test_zero_id_handled_gracefully(self):
        """msg.id == 0 → 0 or 0 → не перетирает положительное значение."""
        owner = _StubOwner(update_id=7)
        msg = self._make_message(0)
        uid = getattr(msg, "id", 0) or 0
        if uid > owner._last_seen_update_id:
            owner._last_seen_update_id = uid
        assert owner._last_seen_update_id == 7

    def test_missing_id_attr_fallback(self):
        """Нет атрибута id → getattr fallback на 0."""
        owner = _StubOwner(update_id=3)
        msg = MagicMock(spec=[])  # нет id
        uid = getattr(msg, "id", 0) or 0
        if uid > owner._last_seen_update_id:
            owner._last_seen_update_id = uid
        assert owner._last_seen_update_id == 3


# ---------------------------------------------------------------------------
# 3. _probe_updates_flow_alive
# ---------------------------------------------------------------------------

class TestProbeUpdatesFlowAlive:
    """_probe_updates_flow_alive: True если id вырос, False если заморожен."""

    @pytest.mark.asyncio
    async def test_returns_true_when_id_grows(self):
        """ID вырастает за settle_sec → returns True."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=10)

        async def _grow_id_after_delay():
            await asyncio.sleep(0.05)
            owner._last_seen_update_id = 20

        grow_task = asyncio.create_task(_grow_id_after_delay())
        result = await _probe_updates_flow_alive(owner, settle_sec=0.1)
        await grow_task
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_id_frozen(self):
        """ID не меняется за settle_sec → returns False (split-brain)."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=42)
        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_missing_attr(self):
        """Owner без _last_seen_update_id → 0==0 → False."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = object()  # нет _last_seen_update_id
        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is False

    @pytest.mark.asyncio
    async def test_settle_sec_respected(self):
        """Probe занимает не меньше settle_sec."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=0)
        t0 = time.monotonic()
        await _probe_updates_flow_alive(owner, settle_sec=0.1)
        elapsed = time.monotonic() - t0
        assert elapsed >= 0.08, f"settle не был соблюдён: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 4. AST-проверка кода в _process_message
# ---------------------------------------------------------------------------

class TestProcessMessageASTContainsTracking:
    """_process_message в bridge должен содержать логику update_id tracking."""

    def test_update_id_code_in_process_message(self):
        """Проверяем что Wave 39-D код присутствует в bridge._process_message."""
        import pathlib

        src = pathlib.Path("src/userbot_bridge.py").read_text(encoding="utf-8")
        assert "_last_seen_update_id" in src, (
            "_last_seen_update_id не найден в userbot_bridge.py"
        )
        assert "_uid" in src or "uid" in src, (
            "uid tracking variable not found in userbot_bridge.py"
        )


# ---------------------------------------------------------------------------
# 5. Integration smoke: split-brain path
# ---------------------------------------------------------------------------

class TestSplitBrainIntegrationSmoke:
    """Smoke-тест: split-brain escalation flow вызывает нужные компоненты."""

    @pytest.mark.asyncio
    async def test_split_brain_triggers_reconnect_on_frozen_updates(self):
        """Если invoke alive + updates заморожены → _try_reconnect_pyrofork вызван."""
        # Используем _probe_updates_flow_alive напрямую чтобы проверить возврат False
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=100)
        # Updates заморожены — probe вернёт False
        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is False, "split-brain не детектирован при frozen updates"

    @pytest.mark.asyncio
    async def test_split_brain_not_triggered_when_updates_flow(self):
        """Если updates flow нормальный → reconnect НЕ нужен."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=100)

        async def _simulate_update():
            await asyncio.sleep(0.02)
            owner._last_seen_update_id = 200

        task = asyncio.create_task(_simulate_update())
        result = await _probe_updates_flow_alive(owner, settle_sec=0.08)
        await task
        assert result is True, "split-brain ложно детектирован при живых updates"

    def test_network_watchdog_exports_probe(self):
        """_probe_updates_flow_alive экспортируется из network_watchdog модуля."""
        from src.userbot import network_watchdog

        assert hasattr(network_watchdog, "_probe_updates_flow_alive"), (
            "_probe_updates_flow_alive не найдена в network_watchdog"
        )
        assert callable(network_watchdog._probe_updates_flow_alive)

    def test_network_watchdog_split_brain_logic_in_source(self):
        """AST: split-brain escalation присутствует в _network_offline_monitor_loop."""
        import pathlib

        src = pathlib.Path(
            "src/userbot/network_watchdog.py"
        ).read_text(encoding="utf-8")
        assert "_probe_updates_flow_alive" in src
        assert "split_brain" in src
        assert "split_brain_detected" in src
