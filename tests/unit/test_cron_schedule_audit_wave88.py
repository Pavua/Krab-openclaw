# -*- coding: utf-8 -*-
"""Wave 88: тесты cron schedule audit script."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts import krab_cron_schedule_audit as cron_audit


def _write_plist_json_stub(tmp_path: Path, label: str, plist_dict: dict) -> Path:
    """Helper: запиcать «фейковый» plist (XML), который plutil сможет прочесть.

    Используем plutil -convert binary1 -o ... через JSON input? Проще: plutil
    умеет читать XML plist directly. Тут пишем XML — но parse_plist_to_dict
    мы будем mock-ать в большинстве тестов через monkeypatch, чтобы не
    зависеть от plutil-binary.
    """
    target = tmp_path / f"{label}.plist"
    target.write_text(f"<plist>stub for {label}</plist>")
    return target


def test_expected_interval_calendar_weekly():
    plist = {"StartCalendarInterval": {"Weekday": 1, "Hour": 10, "Minute": 0}}
    schedule_type, interval = cron_audit.expected_interval_from_schedule(plist)
    assert schedule_type == "calendar_weekly"
    assert interval == cron_audit._SEC_PER_WEEK


def test_expected_interval_calendar_daily():
    plist = {"StartCalendarInterval": {"Hour": 2, "Minute": 7}}
    schedule_type, interval = cron_audit.expected_interval_from_schedule(plist)
    assert schedule_type == "calendar_daily"
    assert interval == cron_audit._SEC_PER_DAY


def test_expected_interval_start_interval():
    plist = {"StartInterval": 300}
    schedule_type, interval = cron_audit.expected_interval_from_schedule(plist)
    assert schedule_type == "interval"
    assert interval == 300.0


def test_expected_interval_no_schedule():
    plist = {"KeepAlive": True, "RunAtLoad": True}
    schedule_type, interval = cron_audit.expected_interval_from_schedule(plist)
    assert schedule_type == "none"
    assert interval is None


def test_audit_single_plist_fresh_log(tmp_path, monkeypatch):
    """Лог свежий — stale_cron=False."""
    log_path = tmp_path / "fresh.out.log"
    log_path.write_text("ok")
    plist_dict = {
        "Label": "ai.krab.fresh",
        "StartInterval": 3600,
        "StandardOutPath": str(log_path),
    }
    monkeypatch.setattr(cron_audit, "parse_plist_to_dict", lambda _p: plist_dict)
    plist_path = tmp_path / "ai.krab.fresh.plist"
    plist_path.write_text("stub")
    result = cron_audit.audit_single_plist(plist_path, time.time())
    assert result is not None
    assert result["label"] == "ai.krab.fresh"
    assert result["stale_cron"] is False
    assert result["log_exists"] is True
    assert result["schedule_type"] == "interval"
    assert result["expected_interval_sec"] == 3600.0


def test_audit_single_plist_stale_log(tmp_path, monkeypatch):
    """Лог сильно старый — stale_cron=True."""
    log_path = tmp_path / "stale.out.log"
    log_path.write_text("old")
    # Установим mtime 10 дней назад при weekly schedule (7d) → > 2× → stale.
    ten_days_ago = time.time() - (10 * 86400)
    import os as _os

    _os.utime(log_path, (ten_days_ago, ten_days_ago))

    plist_dict = {
        "Label": "ai.krab.stale-weekly",
        "StartCalendarInterval": {"Weekday": 1, "Hour": 10},
        "StandardOutPath": str(log_path),
    }
    monkeypatch.setattr(cron_audit, "parse_plist_to_dict", lambda _p: plist_dict)
    plist_path = tmp_path / "ai.krab.stale-weekly.plist"
    plist_path.write_text("stub")
    result = cron_audit.audit_single_plist(plist_path, time.time())
    assert result is not None
    # 10 days > 2 × 7 days? Нет, 10d < 14d → НЕ stale. Сделаем 20 дней:
    twenty_days_ago = time.time() - (20 * 86400)
    _os.utime(log_path, (twenty_days_ago, twenty_days_ago))
    result = cron_audit.audit_single_plist(plist_path, time.time())
    assert result is not None
    assert result["stale_cron"] is True
    assert result["log_exists"] is True


def test_audit_single_plist_missing_log(tmp_path, monkeypatch):
    """Лог отсутствует — stale_cron=True (cron не пишет stdout)."""
    plist_dict = {
        "Label": "ai.krab.no-log",
        "StartInterval": 600,
        "StandardOutPath": str(tmp_path / "nonexistent.log"),
    }
    monkeypatch.setattr(cron_audit, "parse_plist_to_dict", lambda _p: plist_dict)
    plist_path = tmp_path / "ai.krab.no-log.plist"
    plist_path.write_text("stub")
    result = cron_audit.audit_single_plist(plist_path, time.time())
    assert result is not None
    assert result["stale_cron"] is True
    assert result["log_exists"] is False


def test_audit_single_plist_no_schedule(tmp_path, monkeypatch):
    """KeepAlive-only plist — skip (None)."""
    plist_dict = {"Label": "ai.krab.daemon", "KeepAlive": True, "RunAtLoad": True}
    monkeypatch.setattr(cron_audit, "parse_plist_to_dict", lambda _p: plist_dict)
    plist_path = tmp_path / "ai.krab.daemon.plist"
    plist_path.write_text("stub")
    result = cron_audit.audit_single_plist(plist_path, time.time())
    assert result is None


def test_run_audit_aggregates(tmp_path, monkeypatch):
    """run_audit обходит каталог + аккумулирует stale_agents."""
    # Создадим 3 fake plists в tmp_path.
    fresh_log = tmp_path / "fresh.log"
    fresh_log.write_text("x")
    stale_log = tmp_path / "stale.log"
    stale_log.write_text("y")
    import os as _os

    _os.utime(stale_log, (time.time() - 30 * 86400, time.time() - 30 * 86400))

    plist_data_map = {
        "ai.krab.fresh.plist": {
            "Label": "ai.krab.fresh",
            "StartInterval": 3600,
            "StandardOutPath": str(fresh_log),
        },
        "ai.krab.stale.plist": {
            "Label": "ai.krab.stale",
            "StartCalendarInterval": {"Weekday": 1, "Hour": 11},
            "StandardOutPath": str(stale_log),
        },
        "ai.krab.daemon.plist": {
            "Label": "ai.krab.daemon",
            "KeepAlive": True,
        },
    }
    for name in plist_data_map:
        (tmp_path / name).write_text("stub")

    def fake_parse(plist_path: Path):
        return plist_data_map.get(plist_path.name)

    monkeypatch.setattr(cron_audit, "parse_plist_to_dict", fake_parse)

    snapshot = cron_audit.run_audit(plist_dir=tmp_path, now_ts=time.time())
    assert snapshot["total_agents"] == 3
    assert snapshot["scheduled_agents"] == 2  # daemon skipped
    assert snapshot["stale_cron_count"] == 1
    assert snapshot["stale_agents"][0]["label"] == "ai.krab.stale"
    # JSON-serializable
    json.dumps(snapshot)


def test_run_audit_empty_dir(tmp_path):
    """Пустой каталог — пустой snapshot, без ошибок."""
    snapshot = cron_audit.run_audit(plist_dir=tmp_path, now_ts=time.time())
    assert snapshot["total_agents"] == 0
    assert snapshot["scheduled_agents"] == 0
    assert snapshot["stale_cron_count"] == 0
    assert snapshot["stale_agents"] == []


def test_resolve_log_path_fallback():
    """Без StandardOutPath → fallback на <krab_root>/logs/<label>.out.log."""
    plist = {}
    path = cron_audit.resolve_log_path(plist, "ai.krab.foo")
    assert path is not None
    assert path.name == "ai.krab.foo.out.log"


def test_metrics_exposition_renders_cron_schedule_stale(tmp_path, monkeypatch):
    """src.core.metrics.launchd._render_cron_schedule_audit читает snapshot."""
    from src.core.metrics import launchd as launchd_metrics

    snapshot = {
        "all_agents": [
            {"label": "ai.krab.fresh", "stale_cron": False},
            {"label": "ai.krab.stale", "stale_cron": True},
        ]
    }
    snapshot_path = tmp_path / ".openclaw" / "krab_runtime_state" / "cron_schedule_audit.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot))

    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path))
    # Path.home — classmethod в нашем случае? Нет, обычный. Перепишем:
    monkeypatch.setattr(launchd_metrics, "Path", Path)

    # Используем simpler monkeypatch через env hack: создадим snapshot напрямую
    # в реальном HOME — нет, лучше через перехват pathlib.Path.home.
    # Проще: monkeypatch home env. На macOS Path.home() читает HOME env.
    monkeypatch.setenv("HOME", str(tmp_path))

    lines = launchd_metrics._render_cron_schedule_audit(lambda s: s)
    text = "\n".join(lines)
    assert "krab_cron_schedule_stale" in text
    assert 'krab_cron_schedule_stale{label="ai.krab.fresh"} 0' in text
    assert 'krab_cron_schedule_stale{label="ai.krab.stale"} 1' in text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
