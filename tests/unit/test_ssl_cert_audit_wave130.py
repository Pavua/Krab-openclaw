"""Wave 130: тесты SSL cert expiry audit script."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# scripts/ не на стандартном path — добавим repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import krab_ssl_cert_audit as audit  # noqa: E402


def test_parse_not_after_basic():
    """Стандартный peer cert notAfter формат парсится в UTC."""
    dt = audit._parse_not_after("May 12 11:22:33 2026 GMT")
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 12
    assert dt.tzinfo is timezone.utc


def test_compute_days_until_positive():
    """Future expiry → положительное число дней."""
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    expiry = now + timedelta(days=30)
    assert audit._compute_days_until(expiry, now=now) == pytest.approx(30.0, abs=0.01)


def test_compute_days_until_negative_for_expired():
    """Истёкший cert → отрицательное число дней."""
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    expiry = now - timedelta(days=5)
    days = audit._compute_days_until(expiry, now=now)
    assert days < 0
    assert days == pytest.approx(-5.0, abs=0.01)


def test_probe_host_success_with_mock_cert():
    """Mock fetch_peer_cert → days_until_expiry рассчитывается корректно."""
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    fake_cert = {"notAfter": "Jun 12 00:00:00 2026 GMT"}
    with patch.object(audit, "fetch_peer_cert", return_value=fake_cert):
        entry = audit.probe_host("api.anthropic.com", now=now)
    assert entry["host"] == "api.anthropic.com"
    assert entry["expired"] is False
    assert entry["days_until_expiry"] == pytest.approx(31.0, abs=0.5)
    assert "error" not in entry


def test_probe_host_detects_expired():
    """Если notAfter в прошлом — expired=True, days<0."""
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    fake_cert = {"notAfter": "Jan 1 00:00:00 2026 GMT"}
    with patch.object(audit, "fetch_peer_cert", return_value=fake_cert):
        entry = audit.probe_host("api.anthropic.com", now=now)
    assert entry["expired"] is True
    assert entry["days_until_expiry"] < 0


def test_probe_host_handles_oserror_gracefully():
    """Сетевая ошибка → error в entry, не raise."""
    with patch.object(audit, "fetch_peer_cert", side_effect=OSError("connection refused")):
        entry = audit.probe_host("nowhere.invalid")
    assert "error" in entry
    assert "OSError" in entry["error"]


def test_probe_host_missing_not_after():
    """Cert без notAfter → error, no exception."""
    with patch.object(audit, "fetch_peer_cert", return_value={}):
        entry = audit.probe_host("foo.example")
    assert entry.get("error") == "no notAfter in peer cert"


def test_run_audit_aggregates_results():
    """run_audit идёт по списку hosts и возвращает структуру timestamp+hosts."""
    now = datetime(2026, 5, 12, tzinfo=timezone.utc)
    fake_cert = {"notAfter": "Aug 1 00:00:00 2026 GMT"}
    with patch.object(audit, "fetch_peer_cert", return_value=fake_cert):
        report = audit.run_audit(hosts=["a.test", "b.test"], now=now)
    assert "timestamp" in report
    assert len(report["hosts"]) == 2
    assert {e["host"] for e in report["hosts"]} == {"a.test", "b.test"}
    assert all(e["expired"] is False for e in report["hosts"])


def test_persist_report_trims_history(tmp_path):
    """persist_report сохраняет до MAX_HISTORY записей."""
    path = tmp_path / "ssl_cert_audit.json"
    for i in range(audit.MAX_HISTORY + 5):
        audit.persist_report({"timestamp": f"t{i}", "hosts": []}, path=path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["history"]) == audit.MAX_HISTORY
    # последний должен быть наибольший timestamp.
    assert data["history"][-1]["timestamp"] == f"t{audit.MAX_HISTORY + 4}"
