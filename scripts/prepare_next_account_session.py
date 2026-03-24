#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_next_account_session.py — one-click подготовка проекта к продолжению на другой macOS-учётке.

Что делает:
1) Снимает readiness и switchover truth.
2) Публикует fast-path shared worktree `Краб-active`.
3) Сохраняет runtime status без побочных эффектов.
4) Собирает свежий handoff bundle.

Зачем:
- перед переходом на другую учётку пользователю не хочется вручную гонять
  несколько helper-скриптов в правильном порядке;
- этот orchestrator собирает все ключевые артефакты одной командой и не
  трогает live runtime.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OPS_DIR = ROOT / "artifacts" / "ops"


def _run_command(name: str, argv: list[str]) -> dict[str, Any]:
    """Запускает helper и возвращает краткий structured результат."""
    try:
        proc = subprocess.run(
            argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": str(exc),
        }
    return {
        "name": name,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "").strip(),
        "stderr": (proc.stderr or "").strip(),
    }


def _latest_handoff_targets() -> dict[str, str]:
    """Возвращает пути к самой свежей handoff-папке и zip-архиву."""
    artifacts_dir = ROOT / "artifacts"
    latest_dir = ""
    latest_zip = ""
    dirs = sorted((p for p in artifacts_dir.glob("handoff_*") if p.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True)
    zips = sorted((p for p in artifacts_dir.glob("handoff_*.zip") if p.is_file()), key=lambda path: path.stat().st_mtime, reverse=True)
    if dirs:
        latest_dir = str(dirs[0])
    if zips:
        latest_zip = str(zips[0])
    return {
        "dir": latest_dir,
        "zip": latest_zip,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    """Строит короткий human-readable summary для пользователя и следующей учётки."""
    lines = [
        "# Prepare Next Account Session",
        "",
        f"- generated_at_utc: `{payload.get('generated_at_utc', 'unknown')}`",
        f"- ok: `{payload.get('ok', False)}`",
        f"- latest_handoff_dir: `{payload.get('latest_handoff_dir', '-')}`",
        f"- latest_handoff_zip: `{payload.get('latest_handoff_zip', '-')}`",
        "- active_shared_root: `/Users/Shared/Antigravity_AGENTS/Краб-active`",
        "",
        "## Executed steps",
    ]
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        lines.append(
            f"- `{step.get('name')}`: ok=`{step.get('ok')}` rc=`{step.get('returncode')}`"
        )
    lines.extend(
        [
            "",
            "## Что делать дальше",
            "1. На другой учётке открой `/Users/Shared/Antigravity_AGENTS/Краб-active`, если нужен быстрый coding loop без legacy shared drift.",
            "2. Для контекста приложи свежую handoff-папку целиком в новый чат или её zip-архив.",
            "3. Если на другой учётке нужен live runtime, сначала проверь ownership через `Runtime Switch Status.command`.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    steps = [
        ("readiness", [sys.executable, "scripts/check_second_account_readiness.py"]),
        ("shared_repo_switchover", [sys.executable, "scripts/prepare_shared_repo_switchover.py"]),
        ("publish_active_shared_worktree", [sys.executable, "scripts/publish_active_shared_worktree.py"]),
        ("runtime_status", [sys.executable, "scripts/runtime_switch_assistant.py", "status"]),
        ("export_handoff_bundle", [sys.executable, "scripts/export_handoff_bundle.py"]),
    ]

    results = [_run_command(name, argv) for name, argv in steps]
    latest_handoff = _latest_handoff_targets()
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": all(bool(step.get("ok")) for step in results),
        "steps": results,
        "latest_handoff_dir": latest_handoff["dir"],
        "latest_handoff_zip": latest_handoff["zip"],
    }

    latest_json = OPS_DIR / "prepare_next_account_session_latest.json"
    latest_md = OPS_DIR / "prepare_next_account_session_latest.md"
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest_md.write_text(_render_markdown(payload), encoding="utf-8")

    print("=== Prepare Next Account Session ===")
    print(f"ok: {payload['ok']}")
    print(f"latest_handoff_dir: {payload['latest_handoff_dir'] or '-'}")
    print(f"latest_handoff_zip: {payload['latest_handoff_zip'] or '-'}")
    print(f"summary_md: {latest_md}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
