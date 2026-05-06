# -*- coding: utf-8 -*-
"""Wave 36-C tests: fail-loud after N zombie escalations за 24h."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def tmp_history_file(monkeypatch, tmp_path: Path) -> Path:
    """Перехватывает _ZOMBIE_HISTORY_FILE на изолированный tmp."""
    from src.userbot import network_watchdog as nw

    target = tmp_path / "zombie_escalation_history.json"
    monkeypatch.setattr(nw, "_ZOMBIE_HISTORY_FILE", target)
    return target


def test_read_zombie_history_returns_empty_when_no_file(tmp_history_file):
    from src.userbot.network_watchdog import _read_zombie_history

    assert _read_zombie_history() == []


def test_read_zombie_history_returns_empty_on_corrupt_json(tmp_history_file):
    from src.userbot.network_watchdog import _read_zombie_history

    tmp_history_file.write_text("not-valid-json{", encoding="utf-8")
    # fail-open: corrupt → []
    assert _read_zombie_history() == []


def test_record_zombie_escalation_creates_file_with_one_entry(tmp_history_file):
    from src.userbot.network_watchdog import _record_zombie_escalation

    count = _record_zombie_escalation()
    assert count == 1
    assert tmp_history_file.exists()
    history = json.loads(tmp_history_file.read_text())
    assert len(history) == 1
    assert isinstance(history[0], (int, float))


def test_record_zombie_escalation_increments_count(tmp_history_file):
    from src.userbot.network_watchdog import _record_zombie_escalation

    counts = [_record_zombie_escalation() for _ in range(3)]
    assert counts == [1, 2, 3]


def test_record_zombie_escalation_drops_old_entries(tmp_history_file, monkeypatch):
    """Записи старше окна должны вырезаться при следующей записи."""
    from src.userbot import network_watchdog as nw

    # Окно 100s для теста
    monkeypatch.setattr(nw, "_ZOMBIE_FAIL_LOUD_WINDOW_SEC", 100)
    # Первая запись — старая (200s назад)
    old_ts = time.time() - 200
    tmp_history_file.write_text(json.dumps([old_ts]), encoding="utf-8")
    # Новая запись — должна вырезать старую
    count = nw._record_zombie_escalation()
    assert count == 1, "старая запись должна быть отфильтрована"


def test_count_recent_escalations_filters_by_window(tmp_history_file, monkeypatch):
    from src.userbot import network_watchdog as nw

    monkeypatch.setattr(nw, "_ZOMBIE_FAIL_LOUD_WINDOW_SEC", 3600)
    now = time.time()
    # 2 свежих + 2 старых
    tmp_history_file.write_text(
        json.dumps([now - 100, now - 200, now - 7200, now - 8000]),
        encoding="utf-8",
    )
    assert nw._count_recent_escalations() == 2


@pytest.mark.asyncio
async def test_send_zombie_alert_normal_message_below_threshold(tmp_history_file, monkeypatch):
    """recent_escalations < threshold → обычное сообщение (🧟)."""
    from unittest.mock import AsyncMock

    # Устанавливаем threshold=3 для теста
    from src.userbot import network_watchdog as nw
    from src.userbot.network_watchdog import NetworkWatchdogMixin

    monkeypatch.setattr(nw, "_ZOMBIE_FAIL_LOUD_THRESHOLD", 3)

    bot = NetworkWatchdogMixin.__new__(NetworkWatchdogMixin)
    bot._send_proactive_watch_alert = AsyncMock()

    await bot._send_zombie_alert_to_owner(silence_sec=720, consecutive=3, recent_escalations=1)

    bot._send_proactive_watch_alert.assert_awaited_once()
    sent_msg = bot._send_proactive_watch_alert.call_args[0][0]
    assert "🧟" in sent_msg
    assert "🚨" not in sent_msg, "ниже threshold — без КРИТИЧНО"
    assert "Recent zombie escalations: 1/3" in sent_msg


@pytest.mark.asyncio
async def test_send_zombie_alert_fail_loud_at_threshold(tmp_history_file, monkeypatch):
    """recent_escalations == threshold → 🚨 КРИТИЧНО + architectural review hint."""
    from unittest.mock import AsyncMock

    from src.userbot import network_watchdog as nw
    from src.userbot.network_watchdog import NetworkWatchdogMixin

    monkeypatch.setattr(nw, "_ZOMBIE_FAIL_LOUD_THRESHOLD", 3)

    bot = NetworkWatchdogMixin.__new__(NetworkWatchdogMixin)
    bot._send_proactive_watch_alert = AsyncMock()

    await bot._send_zombie_alert_to_owner(silence_sec=720, consecutive=3, recent_escalations=3)

    sent_msg = bot._send_proactive_watch_alert.call_args[0][0]
    assert "🚨" in sent_msg, "fail-loud режим должен быть с 🚨"
    assert "КРИТИЧНО" in sent_msg
    assert "архитектурную проблему" in sent_msg
    assert "Sentry" in sent_msg, "указатель на forensics tool"


def test_record_atomic_via_tmp_rename(tmp_history_file):
    """Запись должна идти через .tmp + rename (не оставлять .tmp file)."""
    from src.userbot.network_watchdog import _record_zombie_escalation

    _record_zombie_escalation()
    # .tmp не должен оставаться
    tmp_artifact = tmp_history_file.with_suffix(".tmp")
    assert not tmp_artifact.exists(), ".tmp файл должен быть rename'нут в финальный"
    assert tmp_history_file.exists()
