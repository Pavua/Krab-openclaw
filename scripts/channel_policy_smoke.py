#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Channel Policy Smoke для Krab/OpenClaw.

Зачем:
1) Дать быстрый единый прогон критичных проверок роутинга по каналам.
2) Подтвердить, что локальный контур автозагружается только когда нужен.
3) Подтвердить предсказуемый cloud fallback при локальных сбоях.

Что проверяет:
- route_stream: runtime text error -> cloud fallback;
- route_stream: force_cloud не трогает local health;
- route_query: force_cloud не трогает local health;
- route_query: auto cloud-primary (reasoning) не трогает local runtime заранее;
- live_channel_smoke (опционально, по флагу).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "ops"


@dataclass
class Step:
    name: str
    cmd: list[str]
    required: bool = True
    timeout_sec: int = 600


def _python_bin() -> str:
    venv_py = ROOT / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable or "python3"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_step(step: Step) -> dict:
    try:
        proc = subprocess.run(
            step.cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(10, int(step.timeout_sec)),
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        tail = "\n".join((out + "\n" + err).strip().splitlines()[-10:]).strip()
        return {
            "name": step.name,
            "required": step.required,
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode),
            "cmd": step.cmd,
            "summary": tail or f"exit={proc.returncode}",
        }
    except subprocess.TimeoutExpired:
        return {
            "name": step.name,
            "required": step.required,
            "ok": False,
            "exit_code": 124,
            "cmd": step.cmd,
            "summary": f"timeout>{step.timeout_sec}s",
        }
    except Exception as exc:
        return {
            "name": step.name,
            "required": step.required,
            "ok": False,
            "exit_code": 1,
            "cmd": step.cmd,
            "summary": f"exception:{type(exc).__name__}:{exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Канальный smoke policy для Krab")
    parser.add_argument(
        "--with-live",
        action="store_true",
        help="Добавить live_channel_smoke.py в конец прогона.",
    )
    args = parser.parse_args()

    py = _python_bin()
    steps: list[Step] = [
        Step(
            name="stream_runtime_text_to_cloud_fallback",
            cmd=[
                py,
                "-m",
                "pytest",
                "-q",
                "tests/test_model_router_stream_fallback.py::test_route_stream_detects_runtime_error_text_chunk_and_fallbacks_to_cloud",
            ],
        ),
        Step(
            name="force_cloud_skips_local_health_stream",
            cmd=[
                py,
                "-m",
                "pytest",
                "-q",
                "tests/test_model_router_stream_fallback.py::test_route_stream_force_cloud_skips_local_health_probe",
            ],
        ),
        Step(
            name="force_cloud_skips_local_health_query",
            cmd=[
                py,
                "-m",
                "pytest",
                "-q",
                "tests/test_model_router_stream_fallback.py::test_route_query_force_cloud_skips_local_health_probe",
            ],
        ),
        Step(
            name="auto_cloud_primary_skips_local_probe",
            cmd=[
                py,
                "-m",
                "pytest",
                "-q",
                "tests/test_model_router_stream_fallback.py::test_route_query_auto_cloud_primary_skips_local_probe",
            ],
        ),
    ]

    if args.with_live:
        steps.append(
            Step(
                name="live_channel_smoke",
                cmd=[py, "scripts/live_channel_smoke.py", "--tail-lines", "140"],
                required=False,
                timeout_sec=120,
            )
        )

    results = [_run_step(step) for step in steps]
    required_failed = [item for item in results if item["required"] and not item["ok"]]
    advisory_failed = [item for item in results if (not item["required"]) and (not item["ok"])]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = ARTIFACTS / f"channel_policy_smoke_{stamp}.json"
    latest_path = ARTIFACTS / "channel_policy_smoke_latest.json"

    payload = {
        "ok": len(required_failed) == 0,
        "generated_at": _now_iso(),
        "with_live": bool(args.with_live),
        "required_failed": [item["name"] for item in required_failed],
        "advisory_failed": [item["name"] for item in advisory_failed],
        "steps": results,
    }
    report_text = json.dumps(payload, ensure_ascii=False, indent=2)
    report_path.write_text(report_text, encoding="utf-8")
    latest_path.write_text(report_text, encoding="utf-8")

    print("🧪 Channel Policy Smoke")
    print(f"- total steps: {len(results)}")
    print(f"- required failed: {len(required_failed)}")
    print(f"- advisory failed: {len(advisory_failed)}")
    print(f"- report: {report_path}")

    if required_failed:
        print("\n❌ Обязательные шаги не пройдены:")
        for item in required_failed:
            print(f"  - {item['name']}: {item['summary']}")
        return 1

    if advisory_failed:
        print("\n⚠️ Диагностические предупреждения:")
        for item in advisory_failed:
            print(f"  - {item['name']}: {item['summary']}")

    print("\n✅ Channel Policy Smoke завершен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

