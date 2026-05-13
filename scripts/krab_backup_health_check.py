#!/usr/bin/env python3
"""Wave 237: CLI для ручного запуска backup health check.

Использование:
    venv/bin/python scripts/krab_backup_health_check.py
    venv/bin/python scripts/krab_backup_health_check.py --json
    KRAB_BACKUP_DRILL_ENABLED=1 venv/bin/python scripts/krab_backup_health_check.py

Выходит с кодом 0 если все проверки прошли, 1 если хотя бы одна fail.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Корень проекта в PYTHONPATH (запуск напрямую без -m).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.backup_health_monitor import run_health_check  # noqa: E402


def _format_text(result: dict) -> str:
    """Человекочитаемый вывод."""
    lines: list[str] = []
    status = "OK" if result.get("ok") else "FAIL"
    lines.append(f"Backup health: {status}")
    lines.append(f"Timestamp: {result.get('timestamp')}")
    lines.append(f"Latest backup: {result.get('latest_backup') or '<none>'}")
    if result.get("failures"):
        lines.append(f"Failures: {', '.join(result['failures'])}")
    lines.append("")
    lines.append("Checks:")
    for check in result.get("checks", []):
        mark = "OK " if check.get("ok") else "ERR"
        name = check.get("name", "?")
        # Сокращённый dump без поля name/ok.
        extras = {k: v for k, v in check.items() if k not in ("name", "ok")}
        extras_str = ", ".join(f"{k}={v}" for k, v in extras.items())
        lines.append(f"  [{mark}] {name}: {extras_str}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wave 237 backup health check")
    parser.add_argument("--json", action="store_true", help="JSON output вместо текста")
    args = parser.parse_args()

    result = run_health_check()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_format_text(result))

    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
