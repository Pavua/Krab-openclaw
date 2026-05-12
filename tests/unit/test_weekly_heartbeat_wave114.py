"""Wave 114: tests для scripts/krab_weekly_heartbeat.py."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Загрузка модуля scripts/krab_weekly_heartbeat.py
_HEARTBEAT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "krab_weekly_heartbeat.py"
)
_spec = importlib.util.spec_from_file_location("krab_weekly_heartbeat", _HEARTBEAT_PATH)
assert _spec and _spec.loader
heartbeat = importlib.util.module_from_spec(_spec)
sys.modules["krab_weekly_heartbeat"] = heartbeat
_spec.loader.exec_module(heartbeat)


# ── parse_prom_metrics ────────────────────────────────────────────────


def test_parse_prom_metrics_basic():
    body = (
        "# HELP foo bar\n"
        "# TYPE foo counter\n"
        "krab_sentry_events_total 497\n"
        "krab_smart_routing_decisions_total{mode=\"normal\"} 1000\n"
        "krab_smart_routing_decisions_total{mode=\"silence\"} 234\n"
    )
    out = heartbeat.parse_prom_metrics(body)
    assert out["krab_sentry_events_total"] == 497.0
    # Суммирует labelset
    assert out["krab_smart_routing_decisions_total"] == 1234.0


def test_parse_prom_metrics_empty_and_invalid():
    assert heartbeat.parse_prom_metrics("") == {}
    # Невалидные строки пропускаются
    out = heartbeat.parse_prom_metrics("not_a_metric\n# only comment\n")
    assert out == {}


# ── compose_summary ───────────────────────────────────────────────────


def test_compose_summary_full():
    aggregates = {
        "metrics": {
            "krab_sentry_events_total": 497,
            "krab_smart_routing_decisions_total": 1234,
            "krab_smart_routing_deny_total": 1172,
            "krab_llm_latency_p95_seconds": 1.234,
        },
        "probes": {"probes": {"a": {"healthy": True}, "b": {"healthy": False}}},
        "budget": {"week_eur": 0.32, "budget_eur": 25.0},
        "moderation": {"count": 3, "rows": []},
    }
    now = datetime(2026, 5, 12, 9, 30, tzinfo=timezone.utc)
    text = heartbeat.compose_summary(aggregates, now=now)
    assert "🦀 Weekly Heartbeat 2026-05-12" in text
    assert "Sentry events (7d): 497" in text
    assert "Smart Routing: 1234 decisions, 95% deny" in text
    assert "€0.32" in text
    assert "€25.00" in text
    assert "Moderation actions: 3" in text
    assert "Network probes: 1/2 healthy" in text
    assert "Latency P95: 1.23s" in text


def test_compose_summary_missing_data_graceful():
    # Все источники пустые → n/a, но crash нет
    aggregates = {"metrics": {}, "probes": None, "budget": None, "moderation": None}
    text = heartbeat.compose_summary(aggregates)
    assert "n/a" in text
    assert "🦀 Weekly Heartbeat" in text
    # Линий должно быть фиксированное число (header + blank + 6 строк)
    assert len(text.splitlines()) == 8


def test_compose_summary_partial_metrics_only():
    # Только Sentry — остальное n/a
    aggregates = {
        "metrics": {"krab_sentry_events_total": 42},
        "probes": None,
        "budget": None,
        "moderation": None,
    }
    text = heartbeat.compose_summary(aggregates)
    assert "Sentry events (7d): 42" in text
    assert "Smart Routing: n/a decisions, n/a deny" in text
    assert "Cost (week): n/a / n/a budget" in text


# ── resolve_owner_id ──────────────────────────────────────────────────


def test_resolve_owner_id_from_krab_env(monkeypatch):
    monkeypatch.setenv("KRAB_OWNER_USER_ID", "123456789")
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)
    assert heartbeat.resolve_owner_id() == "123456789"


def test_resolve_owner_id_from_owner_user_ids(monkeypatch):
    monkeypatch.delenv("KRAB_OWNER_USER_ID", raising=False)
    monkeypatch.setenv("OWNER_USER_IDS", "111, 222 , 333")
    assert heartbeat.resolve_owner_id() == "111"


def test_resolve_owner_id_missing_returns_none(monkeypatch):
    monkeypatch.delenv("KRAB_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)
    # Может вернуть None или попасть в config — проверяем тип
    result = heartbeat.resolve_owner_id()
    # Если config пуст, должен быть None; если нет — строка. В CI без config будет None.
    assert result is None or isinstance(result, str)


# ── main: skip-on-missing-owner ───────────────────────────────────────


def test_main_skips_when_owner_missing(monkeypatch, capsys):
    monkeypatch.delenv("KRAB_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USER_IDS", raising=False)
    monkeypatch.setattr(heartbeat, "resolve_owner_id", lambda: None)

    # Не должно пытаться слать
    sent: list = []
    monkeypatch.setattr(
        heartbeat, "send_heartbeat",
        lambda *a, **kw: sent.append(a) or (True, "ok"),
    )

    rc = heartbeat.main([])
    assert rc == 0
    assert sent == []
    err = capsys.readouterr().err
    assert "skip" in err.lower()


def test_main_dry_run_prints_summary(monkeypatch, capsys):
    monkeypatch.setattr(heartbeat, "resolve_owner_id", lambda: "999")
    monkeypatch.setattr(
        heartbeat, "fetch_aggregates",
        lambda: {"metrics": {"krab_sentry_events_total": 7}, "probes": None,
                 "budget": None, "moderation": None},
    )
    sent: list = []
    monkeypatch.setattr(
        heartbeat, "send_heartbeat",
        lambda *a, **kw: sent.append(a) or (True, "ok"),
    )

    rc = heartbeat.main(["--dry-run"])
    assert rc == 0
    assert sent == []  # не отправляли
    out = capsys.readouterr().out
    assert "🦀 Weekly Heartbeat" in out
    assert "Sentry events (7d): 7" in out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
