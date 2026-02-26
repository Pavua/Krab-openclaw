#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R20 Merge Gate: быстрый интеграционный шлюз для стыковки параллельных задач.

Назначение:
1) дать единый воспроизводимый прогон перед интеграцией результатов Antigravity;
2) отделить обязательные проверки от advisory-проверок;
3) сохранить JSON-отчет в artifacts/ops.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "ops"


@dataclass
class CheckResult:
    name: str
    required: bool
    ok: bool
    duration_ms: int
    detail: str


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _python_bin() -> str:
    local = ROOT / ".venv" / "bin" / "python"
    if local.exists():
        return str(local)
    return "python3"


def _run_cmd(name: str, cmd: list[str], required: bool = True, timeout: int = 240) -> CheckResult:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()
        detail = "\n".join(tail[-8:]) if tail else f"returncode={proc.returncode}"
        if not ok:
            detail = f"returncode={proc.returncode}\n{detail}".strip()
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc) or exc.__class__.__name__
    duration_ms = int((time.monotonic() - started) * 1000)
    return CheckResult(name=name, required=required, ok=ok, duration_ms=duration_ms, detail=detail[:1600])


def _http_probe(name: str, url: str, required: bool, timeout: float = 3.0) -> CheckResult:
    started = time.monotonic()
    req = request.Request(url=url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = int(getattr(resp, "status", 200) or 200)
            ok = 200 <= status < 300
            detail = f"status={status}"
    except urlerror.HTTPError as exc:
        ok = False
        detail = f"http_{int(exc.code or 0)}"
    except Exception as exc:  # noqa: BLE001
        ok = False
        detail = str(exc) or exc.__class__.__name__
    duration_ms = int((time.monotonic() - started) * 1000)
    return CheckResult(name=name, required=required, ok=ok, duration_ms=duration_ms, detail=detail[:600])


def main() -> int:
    parser = argparse.ArgumentParser(description="R20 Merge Gate")
    parser.add_argument("--lite-url", default="http://127.0.0.1:8080/api/health/lite")
    parser.add_argument("--deep-url", default="http://127.0.0.1:8080/api/health")
    parser.add_argument("--skip-http", action="store_true")
    args = parser.parse_args()

    py = _python_bin()
    checks: list[CheckResult] = []

    checks.append(
        _run_cmd(
            name="pytest_targeted_r20",
            cmd=[py, "-m", "pytest", "-q", "tests/test_web_app.py", "tests/test_ecosystem_health.py"],
            required=True,
            timeout=420,
        )
    )
    checks.append(
        _run_cmd(
            name="compile_web_health_modules",
            cmd=[py, "-m", "py_compile", "src/modules/web_app.py", "src/core/ecosystem_health.py"],
            required=True,
            timeout=120,
        )
    )

    if not args.skip_http:
        checks.append(_http_probe("http_lite_health", args.lite_url, required=True, timeout=2.0))
        checks.append(_http_probe("http_deep_health", args.deep_url, required=False, timeout=6.0))

    required_failed = [c for c in checks if c.required and not c.ok]
    advisory_failed = [c for c in checks if (not c.required) and (not c.ok)]
    ok = len(required_failed) == 0

    report: dict[str, Any] = {
        "ok": ok,
        "generated_at": _now_utc(),
        "required_failed": len(required_failed),
        "advisory_failed": len(advisory_failed),
        "checks": [
            {
                "name": c.name,
                "required": c.required,
                "ok": c.ok,
                "duration_ms": c.duration_ms,
                "detail": c.detail,
            }
            for c in checks
        ],
    }

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    report_path = ARTIFACTS / f"r20_merge_gate_{stamp}.json"
    latest_path = ARTIFACTS / "r20_merge_gate_latest.json"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(text, encoding="utf-8")
    latest_path.write_text(text, encoding="utf-8")

    print("🧪 R20 Merge Gate")
    print(f"- ok: {ok}")
    print(f"- required_failed: {len(required_failed)}")
    print(f"- advisory_failed: {len(advisory_failed)}")
    print(f"- report: {report_path}")

    for item in checks:
        status = "OK" if item.ok else "FAIL"
        req = "required" if item.required else "advisory"
        print(f"  - [{status}] {item.name} ({req}, {item.duration_ms}ms) :: {item.detail.splitlines()[0] if item.detail else ''}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
