#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Монитор стабильности HTTP health для Krab Core.

Зачем:
- быстро проверить, держится ли `http://127.0.0.1:8080/api/health` без флапов;
- сохранить машинно-читаемый отчёт в artifacts для handover и отладки.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = ROOT / "artifacts" / "ops"


@dataclass
class HealthSample:
    ts_utc: str
    http_up: bool
    http_status: int
    error: str
    core_pids: list[int]


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_core_pids() -> list[int]:
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid_raw, cmd = parts
        low = cmd.lower()
        if "python" not in low:
            continue
        if "-m src.main" not in cmd and "src/main.py" not in cmd:
            continue
        try:
            pids.append(int(pid_raw))
        except Exception:
            continue
    return sorted(set(pids))


def _probe_http(url: str, timeout: float = 4.0) -> tuple[bool, int, str]:
    req = request.Request(url=url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200) or 200)
            return (200 <= status < 300), status, ""
    except urlerror.HTTPError as exc:
        return False, int(exc.code or 0), f"http_{exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, 0, str(exc) or exc.__class__.__name__


def _analyze(samples: list[HealthSample]) -> dict[str, Any]:
    if not samples:
        return {
            "samples_count": 0,
            "up_count": 0,
            "down_count": 0,
            "first_down_at": None,
            "flaps": 0,
            "ok": False,
        }
    up_count = sum(1 for s in samples if s.http_up)
    down_count = len(samples) - up_count
    first_down = next((s.ts_utc for s in samples if not s.http_up), None)
    flaps = 0
    for i in range(1, len(samples)):
        prev = samples[i - 1].http_up
        cur = samples[i].http_up
        if prev != cur:
            flaps += 1
    return {
        "samples_count": len(samples),
        "up_count": up_count,
        "down_count": down_count,
        "first_down_at": first_down,
        "flaps": flaps,
        "ok": down_count == 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Монитор стабильности HTTP health Krab Core.")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8080/api/health/lite",
        help="URL health endpoint (по умолчанию быстрый liveness endpoint).",
    )
    parser.add_argument("--duration-sec", type=int, default=120, help="Общая длительность наблюдения (сек).")
    parser.add_argument("--interval-sec", type=float, default=2.0, help="Интервал между probe (сек).")
    parser.add_argument("--probe-timeout-sec", type=float, default=4.0, help="Таймаут одного HTTP probe (сек).")
    args = parser.parse_args()

    duration = max(5, int(args.duration_sec))
    interval = max(0.5, float(args.interval_sec))
    probe_timeout = max(1.0, float(args.probe_timeout_sec))
    loops = max(1, int(duration / interval))

    samples: list[HealthSample] = []
    for _ in range(loops):
        http_up, status, err = _probe_http(args.url, timeout=probe_timeout)
        sample = HealthSample(
            ts_utc=_now_utc_iso(),
            http_up=http_up,
            http_status=status,
            error=err,
            core_pids=_get_core_pids(),
        )
        samples.append(sample)
        time.sleep(interval)

    analysis = _analyze(samples)
    report = {
        "ok": analysis["ok"],
        "generated_at": _now_utc_iso(),
        "url": args.url,
        "duration_sec": duration,
        "interval_sec": interval,
        "probe_timeout_sec": probe_timeout,
        "analysis": analysis,
        "samples": [asdict(s) for s in samples],
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    report_path = ARTIFACTS_DIR / f"krab_core_health_watch_{stamp}.json"
    latest_path = ARTIFACTS_DIR / "krab_core_health_watch_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    print("🩺 Krab Core Health Watch")
    print(f"- url: {args.url}")
    print(f"- samples: {analysis['samples_count']}")
    print(f"- up/down: {analysis['up_count']}/{analysis['down_count']}")
    print(f"- flaps: {analysis['flaps']}")
    print(f"- ok: {analysis['ok']}")
    print(f"- report: {report_path}")

    return 0 if bool(analysis["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
