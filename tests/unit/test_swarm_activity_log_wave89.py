# -*- coding: utf-8 -*-
"""Wave 89: SwarmActivityLog (SQLite persistent log + Prometheus metrics)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.swarm_activity_log import SwarmActivityLog


@pytest.fixture()
def log(tmp_path: Path) -> SwarmActivityLog:
    db = tmp_path / "swarm_activity.db"
    return SwarmActivityLog(db_path=db)


def test_log_start_returns_id_and_creates_started_row(log: SwarmActivityLog) -> None:
    aid = log.log_swarm_start("traders", "проанализируй BTC")
    assert isinstance(aid, int)
    assert aid > 0

    rows = log.query_recent(limit=5)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == aid
    assert row["team"] == "traders"
    assert row["topic"] == "проанализируй BTC"
    assert row["status"] == "started"
    assert row["latency_ms"] is None
    assert row["errors"] == []


def test_complete_updates_status_latency_and_errors(log: SwarmActivityLog) -> None:
    aid = log.log_swarm_start("coders", "write bot")
    assert log.log_swarm_complete(
        aid,
        status="done",
        latency_ms=4321,
        artifact_ref="artifacts/2026/05/x.json",
        errors=None,
    ) is True

    rows = log.query_recent(limit=5)
    assert rows[0]["status"] == "done"
    assert rows[0]["latency_ms"] == 4321
    assert rows[0]["artifact_ref"] == "artifacts/2026/05/x.json"
    assert rows[0]["errors"] == []


def test_complete_with_failed_status_and_errors(log: SwarmActivityLog) -> None:
    aid = log.log_swarm_start("analysts", "топик X")
    assert log.log_swarm_complete(
        aid,
        status="failed",
        latency_ms=900,
        errors=["TimeoutError", "RPC 500"],
    ) is True
    rows = log.query_recent(limit=5)
    assert rows[0]["status"] == "failed"
    assert rows[0]["errors"] == ["TimeoutError", "RPC 500"]


def test_complete_none_id_is_noop(log: SwarmActivityLog) -> None:
    # log_swarm_complete на None должен корректно вернуть False
    assert log.log_swarm_complete(None, status="done", latency_ms=100) is False


def test_query_recent_filter_by_team_and_ordering(log: SwarmActivityLog) -> None:
    a1 = log.log_swarm_start("traders", "btc")
    a2 = log.log_swarm_start("coders", "py")
    a3 = log.log_swarm_start("traders", "eth")

    only_traders = log.query_recent(team="traders")
    teams = {r["team"] for r in only_traders}
    assert teams == {"traders"}
    ids = [r["id"] for r in only_traders]
    # Ordering: новейшие первыми (a3, a1)
    assert ids[0] == a3
    assert a1 in ids
    assert a2 not in ids


def test_stats_by_team_success_rate_and_avg_latency(log: SwarmActivityLog) -> None:
    # creative: 2 done (avg 200ms), 1 failed → success_rate = 2/3
    a1 = log.log_swarm_start("creative", "idea1")
    log.log_swarm_complete(a1, status="done", latency_ms=100)
    a2 = log.log_swarm_start("creative", "idea2")
    log.log_swarm_complete(a2, status="done", latency_ms=300)
    a3 = log.log_swarm_start("creative", "idea3")
    log.log_swarm_complete(a3, status="failed", latency_ms=50, errors=["x"])

    stats = log.stats_by_team()
    assert "creative" in stats
    cr = stats["creative"]
    assert cr["done"] == 2
    assert cr["failed"] == 1
    assert cr["avg_latency_ms"] == 200.0
    assert cr["success_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_missing_db_path_graceful_no_crash() -> None:
    # SwarmActivityLog без db_path должен молча no-op'ить
    log = SwarmActivityLog()  # db_path=None
    assert log.log_swarm_start("traders", "x") is None
    assert log.log_swarm_complete(None, status="done") is False
    assert log.query_recent() == []
    assert log.stats_by_team() == {}


def test_malformed_args_clamped_or_defaulted(log: SwarmActivityLog) -> None:
    # team="" → fallback "unknown"; topic очень длинный → обрезается
    aid = log.log_swarm_start("", "X" * 5000)
    assert aid is not None
    rows = log.query_recent()
    assert rows[0]["team"] == "unknown"
    assert len(rows[0]["topic"]) <= 2000

    # latency_ms="abc" → None через except в _complete
    aid2 = log.log_swarm_start("coders", "y")
    log.log_swarm_complete(aid2, status="done", latency_ms="abc")  # type: ignore[arg-type]
    row2 = next(r for r in log.query_recent() if r["id"] == aid2)
    assert row2["latency_ms"] is None
    assert row2["status"] == "done"

    # query_recent limit clamp: limit=0 → defaults to 20
    rows_zero = log.query_recent(limit=0)
    assert isinstance(rows_zero, list)


def test_configure_default_path_reinit(tmp_path: Path) -> None:
    log = SwarmActivityLog()
    log.configure_default_path(tmp_path / "swarm_activity.db")
    aid = log.log_swarm_start("traders", "topic")
    assert aid is not None
    assert log.query_recent()[0]["team"] == "traders"
