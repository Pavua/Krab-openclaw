#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 205: CLI-отчёт по memory snapshots.

Примеры:
  python scripts/krab_memory_leak_report.py
  python scripts/krab_memory_leak_report.py --json
  python scripts/krab_memory_leak_report.py --window-hours 6
  python scripts/krab_memory_leak_report.py --snapshot-now
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Корень репозитория в sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.core.memory_leak_detector import (  # noqa: E402
    analyze_trend,
    capture_snapshot,
    read_snapshots,
    run_once,
    snapshots_path,
)


def _fmt_mb(num_bytes: float) -> str:
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _fmt_ts(ts_or_iso: object) -> str:
    if isinstance(ts_or_iso, (int, float)):
        return datetime.fromtimestamp(float(ts_or_iso), tz=timezone.utc).isoformat()
    return str(ts_or_iso)


def cmd_report(window_hours: int | None, as_json: bool) -> int:
    rows = read_snapshots()
    analysis = analyze_trend(snapshots=rows, window_hours=window_hours)

    if as_json:
        out = {
            "snapshots_path": str(snapshots_path()),
            "snapshots_total": len(rows),
            "analysis": analysis.to_dict(),
            "last_snapshot": rows[-1] if rows else None,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"Snapshots file:   {snapshots_path()}")
    print(f"Total snapshots:  {len(rows)}")
    if not rows:
        print("Нет данных — запустите --snapshot-now или дождитесь background-loop.")
        return 0

    last = rows[-1]
    print()
    print("=== Последний snapshot ===")
    print(f"  Время:      {last.get('iso')}")
    print(f"  PID:        {last.get('pid')}")
    print(f"  RSS:        {_fmt_mb(last.get('rss_bytes', 0))}")
    print(f"  VMS:        {_fmt_mb(last.get('vms_bytes', 0))}")
    print(f"  Swap:       {_fmt_mb(last.get('swap_bytes', 0))}")
    print(f"  Threads:    {last.get('num_threads')}")
    print(f"  Open files: {last.get('open_files')}")
    if last.get("tracemalloc_top"):
        print("  Top allocators (tracemalloc):")
        for entry in last["tracemalloc_top"][:5]:
            f = entry.get("file", "?")
            ln = entry.get("lineno", 0)
            kb = entry.get("size_kb", 0)
            cnt = entry.get("count", 0)
            print(f"    - {f}:{ln}  {kb} KB ({cnt} allocs)")

    print()
    print(f"=== Анализ тренда (окно {analysis.window_hours}h) ===")
    print(f"  Samples в окне:    {analysis.samples}")
    print(f"  First RSS:         {analysis.first_rss_mb:.2f} MB")
    print(f"  Last RSS:          {analysis.last_rss_mb:.2f} MB")
    print(f"  Delta:             {analysis.delta_mb:+.2f} MB")
    print(f"  Duration:          {analysis.duration_hours:.2f} h")
    print(f"  Growth rate:       {analysis.growth_mb_per_hour:+.2f} MB/h")
    print(f"  Threshold:         {analysis.threshold_mb_per_hour:.2f} MB/h")
    print(
        "  Leak suspected:    "
        + ("YES — RSS растёт выше threshold" if analysis.suspected else "no")
    )
    return 0


def cmd_snapshot_now(as_json: bool) -> int:
    result = run_once()
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    snap = result.get("snapshot") or {}
    print(f"Snapshot recorded: {snap.get('iso')}")
    print(f"  RSS:        {_fmt_mb(snap.get('rss_bytes', 0))}")
    print(f"  VMS:        {_fmt_mb(snap.get('vms_bytes', 0))}")
    print(f"  Swap:       {_fmt_mb(snap.get('swap_bytes', 0))}")
    analysis = result.get("analysis") or {}
    print(
        f"  Growth:     {analysis.get('growth_mb_per_hour', 0):.2f} MB/h "
        f"(suspected={analysis.get('suspected', False)})"
    )
    if result.get("sentry_fired"):
        print("  Sentry warning emitted.")
    return 0


def cmd_dry_run(as_json: bool) -> int:
    """Только capture — без записи на диск."""
    snap = capture_snapshot()
    if snap is None:
        print("ERROR: snapshot не получен (psutil unavailable?)", file=sys.stderr)
        return 2
    payload = snap.to_jsonl()
    if as_json:
        print(payload)
        return 0
    data = json.loads(payload)
    print(f"Dry-run snapshot ({data['iso']}):")
    print(f"  RSS:  {_fmt_mb(data['rss_bytes'])}")
    print(f"  VMS:  {_fmt_mb(data['vms_bytes'])}")
    print(f"  Swap: {_fmt_mb(data['swap_bytes'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wave 205: Memory leak detector — отчёт по snapshots."
    )
    parser.add_argument(
        "--snapshot-now",
        action="store_true",
        help="Снять snapshot прямо сейчас и записать в JSONL.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Capture без записи на диск (для тестов).",
    )
    parser.add_argument(
        "--window-hours",
        type=int,
        default=None,
        help="Окно анализа тренда в часах (default из ENV или 24).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывод в JSON.",
    )
    args = parser.parse_args(argv)

    if args.snapshot_now:
        return cmd_snapshot_now(args.json)
    if args.dry_run:
        return cmd_dry_run(args.json)
    return cmd_report(args.window_hours, args.json)


if __name__ == "__main__":
    sys.exit(main())
