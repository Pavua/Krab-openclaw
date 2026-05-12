# -*- coding: utf-8 -*-
"""
Wave 129: тесты для weekly capability cache audit.

Проверяем чистую функцию `run_audit` с инжектируемым fetcher/rng/now_fn,
persist FIFO retention и Prometheus counter helper.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import krab_capability_cache_audit as audit
from src.core import prometheus_metrics

# ---- Хелперы тестового cache --------------------------------------------


def _make_cache_file(tmp_path: Path, entries: dict[str, dict]) -> Path:
    p = tmp_path / "chat_capability_cache.json"
    p.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    return p


def _entry(slow: int | None = 0, voice: bool | None = True, text: bool | None = True) -> dict:
    return {
        "slow_mode_seconds": slow,
        "voice_allowed": voice,
        "text_allowed": text,
        "fetched_at": "2026-05-12T00:00:00+00:00",
    }


# ---- 1: cache loader -----------------------------------------------------


def test_load_cache_missing_file_returns_empty(tmp_path: Path) -> None:
    assert audit.load_cache(tmp_path / "missing.json") == {}


def test_load_cache_filters_non_dict_entries(tmp_path: Path) -> None:
    raw = {"100": _entry(), "bad": "not a dict", "200": _entry(slow=10)}
    p = _make_cache_file(tmp_path, raw)  # type: ignore[arg-type]
    loaded = audit.load_cache(p)
    assert set(loaded.keys()) == {"100", "200"}


# ---- 2: sampling ---------------------------------------------------------


def test_sample_size_respects_cache_size() -> None:
    cache = {"1": _entry(), "2": _entry(), "3": _entry()}
    rng = random.Random(42)
    ids = audit.sample_chat_ids(cache, 10, rng=rng)
    assert len(ids) == 3
    assert set(ids) == {"1", "2", "3"}


# ---- 3: compare ----------------------------------------------------------


def test_compare_entries_detects_voice_diff() -> None:
    cached = _entry(slow=0, voice=True)
    live = _entry(slow=0, voice=False)
    assert audit.compare_entries(cached, live) == ["voice_allowed"]


def test_compare_entries_match_returns_empty() -> None:
    e = _entry(slow=5, voice=True, text=True)
    assert audit.compare_entries(e, dict(e)) == []


# ---- 4: end-to-end run_audit --------------------------------------------


def test_run_audit_counts_mismatches_and_skips(tmp_path: Path) -> None:
    cache = {
        "100": _entry(slow=0, voice=True),
        "200": _entry(slow=10, voice=True),
        "300": _entry(slow=0, voice=False),
    }
    p = _make_cache_file(tmp_path, cache)

    # Fake fetcher: 100 совпадает, 200 — voice flipped, 300 — panel timeout.
    def fake_fetcher(url: str) -> dict | None:
        if url.endswith("/100/capability"):
            return _entry(slow=0, voice=True)
        if url.endswith("/200/capability"):
            return _entry(slow=10, voice=False)  # mismatch
        return None  # skip

    fixed_now = datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc)
    report = audit.run_audit(
        cache_path=p,
        panel_url="http://test",
        sample_size=20,
        fetcher=fake_fetcher,
        rng=random.Random(0),
        now_fn=lambda: fixed_now,
    )
    assert report["total_cached"] == 3
    assert report["sampled"] == 3
    assert report["mismatch_count"] == 1
    assert report["mismatched"][0]["chat_id"] == "200"
    assert "voice_allowed" in report["mismatched"][0]["diff_fields"]
    assert "300" in report["skipped"]
    assert report["timestamp"] == fixed_now.isoformat()


def test_run_audit_empty_cache_returns_zero(tmp_path: Path) -> None:
    p = _make_cache_file(tmp_path, {})
    report = audit.run_audit(
        cache_path=p,
        panel_url="http://test",
        sample_size=20,
        fetcher=lambda url: None,
    )
    assert report["total_cached"] == 0
    assert report["sampled"] == 0
    assert report["mismatched"] == []


# ---- 5: persist FIFO -----------------------------------------------------


def test_persist_report_keeps_last_n(tmp_path: Path) -> None:
    rp = tmp_path / "report.json"
    for i in range(audit.MAX_HISTORY + 3):
        audit.persist_report(
            {"timestamp": f"2026-05-{i:02d}", "mismatch_count": i},
            report_path=rp,
        )
    saved = json.loads(rp.read_text(encoding="utf-8"))
    assert len(saved) == audit.MAX_HISTORY
    # Последний должен быть самый свежий.
    assert saved[-1]["mismatch_count"] == audit.MAX_HISTORY + 2


# ---- 6: Prometheus counter ----------------------------------------------


def test_inc_capability_cache_mismatch_increments() -> None:
    prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER.clear()
    prometheus_metrics.inc_capability_cache_mismatch("audit")
    prometheus_metrics.inc_capability_cache_mismatch("audit")
    prometheus_metrics.inc_capability_cache_mismatch("other")
    assert prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER == {
        "audit": 2,
        "other": 1,
    }


# ---- 7: LaunchAgent plist sanity ----------------------------------------


def test_launchagent_plist_present_and_weekly() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plist = repo_root / "scripts" / "launchagents" / "ai.krab.capability-cache-audit.plist"
    assert plist.exists()
    raw = plist.read_text(encoding="utf-8")
    # Weekday 3 = Wednesday, Hour 6.
    assert "<integer>3</integer>" in raw
    assert "ai.krab.capability-cache-audit" in raw
    assert "krab_capability_cache_audit.py" in raw


# ---- 8: emit_prometheus no-op on zero -----------------------------------


def test_emit_prometheus_no_op_on_zero() -> None:
    prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER.clear()
    audit.emit_prometheus(0)
    assert prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER == {}


@pytest.mark.parametrize("count", [1, 3])
def test_emit_prometheus_invokes_counter(count: int) -> None:
    prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER.clear()
    audit.emit_prometheus(count)
    assert prometheus_metrics._CAPABILITY_CACHE_MISMATCH_COUNTER.get("audit") == count
