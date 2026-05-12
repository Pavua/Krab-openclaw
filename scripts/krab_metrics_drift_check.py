#!/usr/bin/env python3
"""Wave 99: Prometheus metrics drift detector.

Scrape `/metrics` endpoint, parse Prometheus text format, compare metric names
against baseline snapshot. Emit JSON drift report.

Drift trigger conditions:
- current_count < baseline_count * 0.95 (≥5% loss)
- ИЛИ any baseline `krab_*` metric missing in current scrape

Usage:
    python scripts/krab_metrics_drift_check.py
    python scripts/krab_metrics_drift_check.py --url http://127.0.0.1:8080/metrics
    python scripts/krab_metrics_drift_check.py --init  # перезаписать baseline

Exit codes:
    0 — no drift
    1 — drift detected
    2 — scrape/IO error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import httpx

DEFAULT_BASELINE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "metrics_baseline.json"
DEFAULT_URL = "http://127.0.0.1:8080/metrics"
DRIFT_THRESHOLD_PCT = 5.0  # ≥5% loss → flag drift


def parse_prometheus_text(body: str) -> set[str]:
    """Извлечь уникальные имена метрик из Prometheus text exposition format.

    Skip `#` lines (HELP/TYPE). Каждая metric line: `name{labels} value` или
    `name value`. Возвращаем set имён без labels.
    """
    names: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Имя — всё до первого '{', ' ', или '\t'
        cut = len(line)
        for ch in ("{", " ", "\t"):
            idx = line.find(ch)
            if idx != -1 and idx < cut:
                cut = idx
        name = line[:cut].strip()
        if name:
            names.add(name)
    return names


def atomic_write_json(path: Path, payload: dict) -> None:
    """Atomic write через tempfile + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".metrics_baseline_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_baseline(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        metrics = data.get("metrics", [])
        if not isinstance(metrics, list):
            return None
        return {str(x) for x in metrics}
    except (json.JSONDecodeError, OSError):
        return None


def save_baseline(path: Path, names: Iterable[str]) -> None:
    payload = {
        "metrics": sorted(set(names)),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(path, payload)


def detect_drift(current: set[str], baseline: set[str]) -> dict:
    """Вернуть структуру drift-отчёта."""
    missing = sorted(baseline - current)
    new_metrics = sorted(current - baseline)
    baseline_count = len(baseline)
    current_count = len(current)
    if baseline_count > 0:
        drift_pct = (baseline_count - current_count) / baseline_count * 100.0
    else:
        drift_pct = 0.0
    # Specific check: any krab_* baseline metric missing → drift
    krab_missing = [m for m in missing if m.startswith("krab_")]
    has_drift = drift_pct >= DRIFT_THRESHOLD_PCT or bool(krab_missing)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "current_count": current_count,
        "baseline_count": baseline_count,
        "missing": missing,
        "new": new_metrics,
        "drift_pct": round(drift_pct, 2),
        "krab_missing": krab_missing,
        "drift_detected": has_drift,
    }


def fetch_metrics(url: str, timeout: float = 10.0) -> str:
    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--init", action="store_true", help="Перезаписать baseline текущим scrape'ом"
    )
    args = parser.parse_args(argv)

    baseline_path = Path(args.baseline)

    try:
        body = fetch_metrics(args.url, timeout=args.timeout)
    except Exception as exc:
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": f"scrape_failed: {type(exc).__name__}: {exc}",
        }
        print(json.dumps(report, ensure_ascii=False))
        return 2

    current = parse_prometheus_text(body)

    if args.init or not baseline_path.exists():
        save_baseline(baseline_path, current)
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "baseline_initialized",
            "current_count": len(current),
            "baseline_path": str(baseline_path),
        }
        print(json.dumps(report, ensure_ascii=False))
        return 0

    baseline = load_baseline(baseline_path)
    if baseline is None:
        # Corrupted baseline → re-init
        save_baseline(baseline_path, current)
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": "baseline_reinitialized_corrupt",
            "current_count": len(current),
        }
        print(json.dumps(report, ensure_ascii=False))
        return 0

    report = detect_drift(current, baseline)
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["drift_detected"] else 0


if __name__ == "__main__":
    sys.exit(main())
