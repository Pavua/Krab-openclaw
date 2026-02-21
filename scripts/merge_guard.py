# -*- coding: utf-8 -*-
"""
Merge Guard для параллельной разработки.

Пайплайн проверки:
1) overlap ownership (Codex vs Antigravity),
2) ключевые быстрые тесты (по умолчанию),
3) опционально полный smoke (`--full`).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> int:
    print(f"\n$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Krab merge guard")
    parser.add_argument("--full", action="store_true", help="добавить полный smoke_test")
    args = parser.parse_args()

    checks: list[list[str]] = [
        [sys.executable, "scripts/check_workstream_overlap.py"],
        [
            "pytest",
            "tests/test_web_app.py",
            "tests/test_model_router_phase_d.py",
            "tests/test_openclaw_client_health.py",
        ],
    ]
    if args.full:
        checks.append([sys.executable, "tests/smoke_test.py"])

    for cmd in checks:
        code = _run(cmd)
        if code != 0:
            print("\n❌ Merge guard failed.")
            return code

    print("\n✅ Merge guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

