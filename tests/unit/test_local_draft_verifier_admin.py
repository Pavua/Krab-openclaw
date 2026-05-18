# -*- coding: utf-8 -*-
"""Tests for /api/admin/local-draft-verifier-stats (S58).

Endpoint reads ``local_draft_verify_divergence_score`` events from
``krab_main.log`` and builds rolling 24h histogram + last-10 samples.
Tests cover: envelope shape, zero samples, populated histogram, cache TTL,
missing log file, corrupt lines.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import health_router as health_module
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.health_router import build_health_router

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_app() -> FastAPI:
    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=lambda *a, **kw: None,
    )
    router = build_health_router(ctx)
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture(autouse=True)
def _clear_cache_and_env(monkeypatch):
    """Clear module-level cache + sensible env defaults before each test."""
    health_module._LDV_CACHE["ts"] = 0.0
    health_module._LDV_CACHE["payload"] = None
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "1")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "0.2")
    yield
    health_module._LDV_CACHE["ts"] = 0.0
    health_module._LDV_CACHE["payload"] = None


def _write_log(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _event_line(
    *, ts: str, score: int, model: str = "lm-studio-local/gemma", req: str = "r-1"
) -> str:
    """Build a structlog ConsoleRenderer line matching production output.

    ANSI codes intentionally included to verify the parser strips them.
    """
    return (
        f"\x1b[2m{ts}\x1b[0m [\x1b[32m\x1b[1minfo     \x1b[0m] "
        f"\x1b[1mlocal_draft_verify_divergence_score    \x1b[0m "
        f"\x1b[36mdivergence_score\x1b[0m=\x1b[35m{score}\x1b[0m "
        f"\x1b[36mlocal_model\x1b[0m=\x1b[35m{model}\x1b[0m "
        f"\x1b[36mrequest_id\x1b[0m=\x1b[35m{req}\x1b[0m "
        f"\x1b[36mquality_score\x1b[0m=\x1b[35m{10 - score}\x1b[0m"
    )


# ── Tests ─────────────────────────────────────────────────────────────────


def test_endpoint_returns_200_with_envelope(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    _write_log(log_file, [])
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))

    app = _make_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/admin/local-draft-verifier-stats")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert data["enabled"] is True
    assert data["sample_rate"] == pytest.approx(0.2)
    assert "stats" in data
    stats = data["stats"]
    assert set(stats.keys()) == {
        "total_verified_24h",
        "divergence_histogram",
        "last_10_samples",
        "mean_score",
        "median_score",
    }
    assert set(stats["divergence_histogram"].keys()) == {"0-2", "3-5", "6-8", "9-10"}
    assert isinstance(data["warnings"], list)


def test_endpoint_zero_samples_returns_null_means(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    _write_log(log_file, ["some unrelated line", "pyrogram NetworkTask started"])
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))

    app = _make_app()
    client = TestClient(app)
    data = client.get("/api/admin/local-draft-verifier-stats").json()

    assert data["stats"]["total_verified_24h"] == 0
    assert data["stats"]["mean_score"] is None
    assert data["stats"]["median_score"] is None
    assert data["stats"]["last_10_samples"] == []
    assert all(v == 0 for v in data["stats"]["divergence_histogram"].values())


def test_endpoint_populates_histogram_from_mock_log(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    now = datetime.now()
    # 6 samples covering each histogram bucket.
    samples = [
        (now - timedelta(minutes=5), 0, "r-a"),  # 0-2
        (now - timedelta(minutes=10), 2, "r-b"),  # 0-2
        (now - timedelta(minutes=15), 4, "r-c"),  # 3-5
        (now - timedelta(minutes=20), 7, "r-d"),  # 6-8
        (now - timedelta(minutes=25), 9, "r-e"),  # 9-10
        (now - timedelta(minutes=30), 10, "r-f"),  # 9-10
    ]
    lines = [
        _event_line(ts=t.strftime("%Y-%m-%d %H:%M:%S"), score=s, req=r) for (t, s, r) in samples
    ]
    # Add an out-of-window sample (>24h old) — должен быть отброшен.
    old_ts = (now - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(_event_line(ts=old_ts, score=5, req="r-old"))
    _write_log(log_file, lines)
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))

    app = _make_app()
    client = TestClient(app)
    data = client.get("/api/admin/local-draft-verifier-stats").json()

    stats = data["stats"]
    assert stats["total_verified_24h"] == 6
    hist = stats["divergence_histogram"]
    assert hist["0-2"] == 2
    assert hist["3-5"] == 1
    assert hist["6-8"] == 1
    assert hist["9-10"] == 2
    # mean = (0+2+4+7+9+10) / 6 = 5.33
    assert stats["mean_score"] == pytest.approx(5.33, abs=0.01)
    assert stats["median_score"] is not None
    # Last-10 sorted newest first; "r-a" is newest in our set.
    assert len(stats["last_10_samples"]) == 6
    assert stats["last_10_samples"][0]["request_id"] == "r-a"
    assert stats["last_10_samples"][0]["model"] == "lm-studio-local/gemma"


def test_endpoint_cache_ttl_avoids_reparse(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    now = datetime.now()
    _write_log(log_file, [_event_line(ts=now.strftime("%Y-%m-%d %H:%M:%S"), score=4, req="r-1")])
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))

    app = _make_app()
    client = TestClient(app)

    first = client.get("/api/admin/local-draft-verifier-stats").json()
    assert first["stats"]["total_verified_24h"] == 1

    # Mutate log; without cache we'd see total=2. With cache (60s TTL,
    # autouse fixture has zeroed the cache ts just before — but the first
    # request populated it), we should still see total=1.
    _write_log(
        log_file,
        [
            _event_line(ts=now.strftime("%Y-%m-%d %H:%M:%S"), score=4, req="r-1"),
            _event_line(ts=now.strftime("%Y-%m-%d %H:%M:%S"), score=6, req="r-2"),
        ],
    )

    second = client.get("/api/admin/local-draft-verifier-stats").json()
    assert second["stats"]["total_verified_24h"] == 1, "cache should have served stale value"

    # Force re-parse via direct call with cache_ttl_sec=0.
    fresh = health_module.collect_local_draft_verifier_stats(cache_ttl_sec=0)
    assert fresh["stats"]["total_verified_24h"] == 2


def test_endpoint_handles_log_file_missing(tmp_path, monkeypatch):
    missing = tmp_path / "does_not_exist.log"
    monkeypatch.setenv("KRAB_LOG_FILE", str(missing))

    app = _make_app()
    client = TestClient(app)
    resp = client.get("/api/admin/local-draft-verifier-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["stats"]["total_verified_24h"] == 0
    assert any("log_file_missing" in w for w in data["warnings"])


def test_endpoint_handles_corrupt_log_lines_gracefully(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    now = datetime.now()
    good_line = _event_line(ts=now.strftime("%Y-%m-%d %H:%M:%S"), score=3, req="r-good")
    lines = [
        "",  # empty
        "garbage no timestamp at all",
        "9999-99-99 99:99:99 [info] local_draft_verify_divergence_score divergence_score=5",  # bad date
        "2026-05-18 03:17:39 [info] local_draft_verify_divergence_score divergence_score=99",  # out of range
        "2026-05-18 03:17:39 [info] local_draft_verify_divergence_score divergence_score=abc",  # not int
        good_line,
        "\xff\xfe\x00 binary trash",
    ]
    _write_log(log_file, lines)
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))

    app = _make_app()
    client = TestClient(app)
    data = client.get("/api/admin/local-draft-verifier-stats").json()
    assert data["ok"] is True
    # Only the good line should count.
    assert data["stats"]["total_verified_24h"] == 1
    assert data["stats"]["last_10_samples"][0]["request_id"] == "r-good"
    assert data["stats"]["last_10_samples"][0]["score"] == 3


def test_endpoint_reflects_disabled_env(tmp_path, monkeypatch):
    log_file = tmp_path / "krab_main.log"
    _write_log(log_file, [])
    monkeypatch.setenv("KRAB_LOG_FILE", str(log_file))
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_ENABLED", "0")
    monkeypatch.setenv("KRAB_LOCAL_DRAFT_VERIFY_SAMPLE_RATE", "0.5")

    app = _make_app()
    client = TestClient(app)
    data = client.get("/api/admin/local-draft-verifier-stats").json()
    assert data["enabled"] is False
    assert data["sample_rate"] == pytest.approx(0.5)
