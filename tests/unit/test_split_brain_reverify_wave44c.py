# -*- coding: utf-8 -*-
"""
Wave 44-C: post-reconnect verification of updates_subscriber flow.

Bug fix для Wave 39-D false-success: `_try_reconnect_pyrofork` мог вернуть
True (TCP/MTProto handshake восстановлен), но updates_subscriber stream
оставался мёртв. Watchdog логировал "split_brain_resolved_via_reconnect",
а в реальности incoming messages всё равно не доходили.

Production observed twice on 2026-05-09 (06:58, 18:46) — Krab posted
"split_brain_resolved_via_reconnect" в логе, но владелец не получал
ответов на DM минуты/часы спустя.

Wave 44-C: после `_try_reconnect_pyrofork` возвращает True — повторно
проверяем updates_flow через `_probe_updates_flow_alive(settle_sec=15)`.
Если update_id всё равно frozen → логируем
`split_brain_reconnect_did_not_restore_updates` и эскалируем в
`_launchd_exit_78()` (full process respawn).
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

_SRC_PATH = pathlib.Path("src/userbot/network_watchdog.py")
_SRC = _SRC_PATH.read_text(encoding="utf-8") if _SRC_PATH.exists() else ""


class _StubOwner:
    """Minimal duck-type для _probe_updates_flow_alive."""

    def __init__(self, update_id: int = 0) -> None:
        self._last_seen_update_id = update_id


# ---------------------------------------------------------------------------
# 1. AST/source-level checks: Wave 44-C должна быть видна в коде
# ---------------------------------------------------------------------------

class TestWave44CSourcePresent:
    """Проверяем, что в _network_offline_monitor_loop есть post-reconnect re-probe."""

    def test_reconnect_did_not_restore_log_key_present(self) -> None:
        """Логирование `split_brain_reconnect_did_not_restore_updates` должно быть."""
        assert "split_brain_reconnect_did_not_restore_updates" in _SRC, (
            "Wave 44-C escalation log key отсутствует в network_watchdog.py"
        )

    def test_reconnect_branch_does_reverify_before_resolved(self) -> None:
        """Должно быть >=2 вызова _probe_updates_flow_alive (split-brain detect + post-reconnect verify)."""
        # Wave 39-D ставит первый probe (detect frozen update_id).
        # Wave 44-C добавляет второй (после _try_reconnect_pyrofork) для verify.
        probe_count = _SRC.count('_probe_updates_flow_alive(')
        assert probe_count >= 2, (
            f"expected >=2 probe call sites for Wave 44-C, found {probe_count}"
        )

    def test_reverify_appears_between_reconnect_and_resolved(self) -> None:
        """Sequence в источнике: _try_reconnect_pyrofork → _probe_updates_flow_alive → split_brain_resolved_via_reconnect."""
        idx_reconnect = _SRC.find('_try_reconnect_pyrofork(self.client)')
        idx_resolved = _SRC.find('split_brain_resolved_via_reconnect')
        assert idx_reconnect > 0 and idx_resolved > idx_reconnect, "anchors not found in expected order"
        between = _SRC[idx_reconnect:idx_resolved]
        assert '_probe_updates_flow_alive' in between, (
            "Wave 44-C re-probe не вставлен между reconnect attempt и resolved log"
        )

    def test_post_reconnect_verify_helper_or_inline(self) -> None:
        """Должна быть либо helper-функция _verify_post_reconnect_*, либо inline re-probe."""
        has_helper = "_verify_post_reconnect" in _SRC
        # alternatively check for a verified flag in resolved log call
        has_verified_flag = 'verified=True' in _SRC or 'verified=' in _SRC
        # AND must reference re-probe in success path
        has_reprobe_keyword = (
            "Wave 44-C" in _SRC or "post_reconnect_verify" in _SRC
        )
        assert has_helper or has_verified_flag or has_reprobe_keyword, (
            "Wave 44-C marker (helper / verified= / 'Wave 44-C' comment) not found"
        )


# ---------------------------------------------------------------------------
# 2. Behavioral test: re-probe возвращает False при frozen update_id
# ---------------------------------------------------------------------------

class TestPostReconnectVerify:
    """Verify-after-reconnect должен распознать тот же frozen-update_id state."""

    @pytest.mark.asyncio
    async def test_frozen_update_id_after_reconnect_returns_false(self) -> None:
        """Reconnect был, но update_id всё ещё не двигается → verify должен вернуть False."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=767230)
        # Имитируем: после reconnect ничего не пришло за settle_sec.
        result = await _probe_updates_flow_alive(owner, settle_sec=0.05)
        assert result is False, (
            "Wave 39-D bug repro: re-probe должен видеть frozen update_id как False"
        )

    @pytest.mark.asyncio
    async def test_moving_update_id_after_reconnect_returns_true(self) -> None:
        """Reconnect был, update_id двинулся → verify должен вернуть True."""
        from src.userbot.network_watchdog import _probe_updates_flow_alive

        owner = _StubOwner(update_id=767230)

        async def _simulate_traffic_arrival() -> None:
            await asyncio.sleep(0.02)
            owner._last_seen_update_id = 767245

        task = asyncio.create_task(_simulate_traffic_arrival())
        result = await _probe_updates_flow_alive(owner, settle_sec=0.08)
        await task
        assert result is True, (
            "verify ложно failed при движении update_id после reconnect"
        )
