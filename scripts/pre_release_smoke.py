# -*- coding: utf-8 -*-
"""
Pre-release smoke для Krab.

Зачем:
1. Дать один вход для предрелизной проверки перед интеграцией/пушем.
2. Разделить проверки на обязательные (gate) и диагностические (advisory).
3. Сохранять отчет в artifacts/ops, чтобы можно было быстро приложить в handover.

Связь с проектом:
- использует текущие guard-команды (`check_workstream_overlap`, `merge_guard`);
- дополняет их runtime-диагностикой каналов OpenClaw и маршрута алертов.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "ops"


@dataclass
class StepResult:
    name: str
    required: bool
    ok: bool
    exit_code: int
    cmd: list[str]
    summary: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def _mk_result(
    name: str,
    cmd: list[str],
    required: bool,
    code: int,
    out: str,
    err: str,
) -> StepResult:
    ok = code == 0
    text = (out.strip() or err.strip() or f"exit={code}")
    summary = "\n".join(text.splitlines()[-6:]).strip()
    return StepResult(
        name=name,
        required=required,
        ok=ok,
        exit_code=code,
        cmd=cmd,
        summary=summary,
    )


def _python_bin() -> str:
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    return sys.executable or "python3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Krab pre-release smoke runner")
    parser.add_argument("--full", action="store_true", help="добавить полный smoke_test")
    parser.add_argument(
        "--strict-runtime",
        action="store_true",
        help="падать, если runtime-диагностика каналов неуспешна",
    )
    args = parser.parse_args()

    py = _python_bin()
    steps: list[StepResult] = []

    # Обязательные гейты.
    required_checks: list[tuple[str, list[str]]] = [
        ("workstream_overlap", [py, "scripts/check_workstream_overlap.py"]),
        ("merge_guard", [py, "scripts/merge_guard.py"]),
    ]
    if args.full:
        required_checks.append(("merge_guard_full", [py, "scripts/merge_guard.py", "--full"]))

    for name, cmd in required_checks:
        code, out, err = _run(cmd, timeout=900)
        steps.append(_mk_result(name, cmd, True, code, out, err))

    # Диагностические проверки runtime (по умолчанию advisory).
    runtime_required = bool(args.strict_runtime)
    diagnostics: list[tuple[str, list[str]]] = []

    diagnostics.append(
        (
            "autoswitch_dry_run",
            [py, "scripts/openclaw_model_autoswitch.py", "--dry-run"],
        )
    )

    if shutil.which("openclaw"):
        diagnostics.append(("channels_probe", ["openclaw", "channels", "status", "--probe"]))
    else:
        steps.append(
            StepResult(
                name="channels_probe",
                required=runtime_required,
                ok=False,
                exit_code=127,
                cmd=["openclaw", "channels", "status", "--probe"],
                summary="openclaw CLI не найден в PATH",
            )
        )

    if (ROOT / "scripts" / "check_signal_alert_route.command").exists():
        diagnostics.append(("signal_alert_route", ["./scripts/check_signal_alert_route.command"]))

    for name, cmd in diagnostics:
        code, out, err = _run(cmd, timeout=240)
        steps.append(_mk_result(name, cmd, runtime_required, code, out, err))

    required_failed = [s for s in steps if s.required and not s.ok]
    advisory_failed = [s for s in steps if (not s.required) and (not s.ok)]

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = ARTIFACTS / f"pre_release_smoke_{stamp}.json"
    latest_path = ARTIFACTS / "pre_release_smoke_latest.json"

    payload = {
        "ok": len(required_failed) == 0,
        "generated_at": _now_iso(),
        "strict_runtime": bool(args.strict_runtime),
        "full": bool(args.full),
        "required_failed": [s.name for s in required_failed],
        "advisory_failed": [s.name for s in advisory_failed],
        "steps": [
            {
                "name": s.name,
                "required": s.required,
                "ok": s.ok,
                "exit_code": s.exit_code,
                "cmd": s.cmd,
                "summary": s.summary,
            }
            for s in steps
        ],
    }
    report_text = json.dumps(payload, ensure_ascii=False, indent=2)
    report_path.write_text(report_text, encoding="utf-8")
    latest_path.write_text(report_text, encoding="utf-8")

    print("🧪 Pre-release Smoke")
    print(f"- required checks: {len([s for s in steps if s.required])}")
    print(f"- advisory checks: {len([s for s in steps if not s.required])}")
    print(f"- required failed: {len(required_failed)}")
    print(f"- advisory failed: {len(advisory_failed)}")
    print(f"- report: {report_path}")

    if required_failed:
        print("\n❌ Обязательные проверки не пройдены:")
        for item in required_failed:
            print(f"  - {item.name}: {item.summary}")
        return 1

    if advisory_failed:
        print("\n⚠️ Диагностические предупреждения:")
        for item in advisory_failed:
            print(f"  - {item.name}: {item.summary}")

    print("\n✅ Pre-release smoke завершен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
