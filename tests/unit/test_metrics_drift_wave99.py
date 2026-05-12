"""Wave 99: тесты drift-детектора Prometheus metrics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.krab_metrics_drift_check import (
    DRIFT_THRESHOLD_PCT,
    atomic_write_json,
    detect_drift,
    load_baseline,
    main,
    parse_prometheus_text,
    save_baseline,
)

SAMPLE_METRICS = """\
# HELP krab_archive_messages_total Total archived messages
# TYPE krab_archive_messages_total counter
krab_archive_messages_total 12345
# HELP krab_memory_validator_pending Pending validator items
# TYPE krab_memory_validator_pending gauge
krab_memory_validator_pending{shard="a"} 5
krab_memory_validator_pending{shard="b"} 3
# Comment line
http_requests_total{method="get",code="200"} 99
process_cpu_seconds_total 1.23

"""


def test_parse_prometheus_text_extracts_unique_names():
    names = parse_prometheus_text(SAMPLE_METRICS)
    assert names == {
        "krab_archive_messages_total",
        "krab_memory_validator_pending",
        "http_requests_total",
        "process_cpu_seconds_total",
    }


def test_parse_skips_comments_and_blank_lines():
    body = "# HELP foo\n# TYPE foo counter\n\n   \nfoo 1\n"
    assert parse_prometheus_text(body) == {"foo"}


def test_detect_drift_no_change():
    baseline = {"krab_a", "krab_b", "krab_c"}
    current = {"krab_a", "krab_b", "krab_c"}
    rep = detect_drift(current, baseline)
    assert rep["drift_detected"] is False
    assert rep["missing"] == []
    assert rep["new"] == []
    assert rep["drift_pct"] == 0.0


def test_detect_drift_krab_metric_missing_triggers():
    baseline = {"krab_a", "krab_b", "krab_c", "krab_d", "krab_e"}
    current = {"krab_a", "krab_b", "krab_c", "krab_d"}  # 20% loss + krab_e missing
    rep = detect_drift(current, baseline)
    assert rep["drift_detected"] is True
    assert "krab_e" in rep["missing"]
    assert rep["krab_missing"] == ["krab_e"]
    assert rep["drift_pct"] >= DRIFT_THRESHOLD_PCT


def test_detect_drift_under_threshold_no_krab_missing():
    # 100 baseline, 1 missing (non-krab) → 1% < 5%, no krab_* missing
    baseline = {f"other_{i}" for i in range(99)} | {"foo_bar"}
    current = {f"other_{i}" for i in range(99)}
    rep = detect_drift(current, baseline)
    assert rep["drift_detected"] is False
    assert rep["drift_pct"] == 1.0


def test_detect_drift_new_metrics_listed():
    baseline = {"a", "b"}
    current = {"a", "b", "krab_new"}
    rep = detect_drift(current, baseline)
    assert rep["new"] == ["krab_new"]
    assert rep["drift_detected"] is False  # new metrics не триггерят


def test_save_and_load_baseline_roundtrip(tmp_path: Path):
    path = tmp_path / "baseline.json"
    save_baseline(path, ["zeta", "alpha", "mu"])
    loaded = load_baseline(path)
    assert loaded == {"zeta", "alpha", "mu"}
    raw = json.loads(path.read_text())
    # Sorted on disk
    assert raw["metrics"] == ["alpha", "mu", "zeta"]
    assert "created_at" in raw


def test_load_baseline_missing_returns_none(tmp_path: Path):
    assert load_baseline(tmp_path / "nope.json") is None


def test_load_baseline_corrupt_returns_none(tmp_path: Path):
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    assert load_baseline(path) is None


def test_atomic_write_json_no_temp_file_leak(tmp_path: Path):
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1})
    # Только сам файл; никаких .metrics_baseline_* временных
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == []
    assert json.loads(path.read_text()) == {"a": 1}


def test_main_init_creates_baseline(tmp_path: Path, capsys):
    baseline = tmp_path / "baseline.json"
    with patch("scripts.krab_metrics_drift_check.fetch_metrics", return_value=SAMPLE_METRICS):
        rc = main(["--baseline", str(baseline), "--init"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["event"] == "baseline_initialized"
    assert baseline.exists()


def test_main_detects_drift(tmp_path: Path, capsys):
    baseline_path = tmp_path / "baseline.json"
    # baseline includes krab_extra; scrape пропустит его
    save_baseline(
        baseline_path,
        [
            "krab_archive_messages_total",
            "krab_memory_validator_pending",
            "http_requests_total",
            "process_cpu_seconds_total",
            "krab_extra_metric",
        ],
    )
    with patch("scripts.krab_metrics_drift_check.fetch_metrics", return_value=SAMPLE_METRICS):
        rc = main(["--baseline", str(baseline_path)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["drift_detected"] is True
    assert "krab_extra_metric" in payload["missing"]


def test_main_scrape_error_returns_2(tmp_path: Path, capsys):
    baseline = tmp_path / "baseline.json"
    with patch(
        "scripts.krab_metrics_drift_check.fetch_metrics",
        side_effect=RuntimeError("boom"),
    ):
        rc = main(["--baseline", str(baseline)])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out)
    assert "error" in payload
    assert "boom" in payload["error"]
