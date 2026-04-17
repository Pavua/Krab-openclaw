#!/usr/bin/env python3
"""Verify /metrics endpoint returns all expected metrics.

Usage:
    python scripts/prometheus_metrics_smoke.py
    python scripts/prometheus_metrics_smoke.py --url http://127.0.0.1:8080/metrics

Exit codes:
    0 — все EXPECTED метрики присутствуют
    1 — ошибка (network / HTTP / missing metric)
"""

from __future__ import annotations

import argparse
import sys

import httpx

# Имена, которые ОБЯЗАНЫ присутствовать (остальные — optional: memory_validator,
# reminders_queue, auto_restart — могут быть отключены по feature-flag).
EXPECTED = [
    "krab_memory_validator_safe_total",
    "krab_memory_validator_pending",
    "krab_archive_messages_total",
    "krab_archive_db_size_bytes",
    "krab_metrics_generated_at",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test Krab /metrics endpoint")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/metrics",
        help="URL to /metrics endpoint",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout (sec)")
    args = parser.parse_args()

    try:
        r = httpx.get(args.url, timeout=args.timeout)
    except Exception as e:
        print(f"FAIL: failed to fetch {args.url}: {e}")
        return 1

    if r.status_code != 200:
        print(f"FAIL: status {r.status_code}")
        return 1

    body = r.text
    missing = [m for m in EXPECTED if m not in body]
    if missing:
        print(f"FAIL: missing metrics: {missing}")
        return 1

    print(f"OK: all {len(EXPECTED)} expected metrics present")
    print(f"Size: {len(body)} bytes, lines: {body.count(chr(10))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
