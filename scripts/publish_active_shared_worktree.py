#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
publish_active_shared_worktree.py — публикует из текущей копии готовую active shared worktree.

Что делает:
1) Копирует текущий репозиторий в отдельный shared path вместе с `.git`.
2) Исключает только тяжёлые локальные кэши и virtualenv.
3) Пишет machine-readable marker, чтобы другая учётка видела branch/HEAD/source.

Зачем:
- старый shared repo уже содержит параллельный USER3 WIP и пока не готов как
  безболезненный daily coding root;
- пользователю нужна быстрая shared-копия, максимально близкая к текущему
  состоянию `pablito`, без ручного merge прямо сейчас;
- active shared worktree снимает конфликт между "нужна shared path" и
  "legacy shared repo уже ушёл в другой branch/WIP".
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_SHARED_ROOT = Path("/Users/Shared/Antigravity_AGENTS/Краб-active")
OPS_DIR = ROOT / "artifacts" / "ops"

EXCLUDES = [
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    "logs/",
    "artifacts/handoff_*/",
    "artifacts/*.zip",
]


def _render_markdown(marker: dict[str, object]) -> str:
    """Строит короткий human-readable отчёт для active shared worktree."""
    return "\n".join(
        [
            "# Active Shared Worktree",
            "",
            f"- generated_at_utc: `{marker.get('generated_at_utc', 'unknown')}`",
            f"- source_root: `{marker.get('source_root', 'unknown')}`",
            f"- active_shared_root: `{marker.get('active_shared_root', 'unknown')}`",
            f"- branch: `{marker.get('branch', 'unknown')}`",
            f"- head: `{marker.get('head', 'unknown')}`",
            "",
            "## Назначение",
            "- Эта shared-копия нужна как быстрый и безопасный coding-root для другой macOS-учётки,",
            "- когда legacy shared repo ещё не reconciled и продолжать из него рискованно.",
            "",
            "## Как использовать",
            f"1. Открой `{marker.get('active_shared_root', 'unknown')}` с другой учётки.",
            "2. Работай с кодом, docs и tests из этой копии.",
            "3. Runtime/auth/browser state всё равно держи только в своём HOME.",
            "4. Перед возвратом на основную учётку снова собери switchover + handoff.",
        ]
    ) + "\n"


def _git_stdout(args: list[str], *, cwd: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _ensure_rsync() -> str:
    rsync = shutil.which("rsync")
    if not rsync:
        raise RuntimeError("rsync_not_found")
    return rsync


def _rsync_repo(src: Path, dst: Path) -> None:
    rsync = _ensure_rsync()
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [rsync, "-a", "--delete", "--no-perms", "--no-owner", "--no-group"]
    for pattern in EXCLUDES:
        cmd.extend(["--exclude", pattern])
    cmd.extend([f"{src}/", str(dst)])
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "rsync_failed")


def main() -> int:
    _rsync_repo(ROOT, ACTIVE_SHARED_ROOT)

    marker = {
        "ok": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": str(ROOT),
        "active_shared_root": str(ACTIVE_SHARED_ROOT),
        "branch": _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=ACTIVE_SHARED_ROOT),
        "head": _git_stdout(["rev-parse", "HEAD"], cwd=ACTIVE_SHARED_ROOT),
        "status_short": _git_stdout(["status", "--short", "--branch"], cwd=ACTIVE_SHARED_ROOT),
    }

    ACTIVE_SHARED_ROOT.joinpath("ACTIVE_SHARED_WORKTREE.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    OPS_DIR.joinpath("active_shared_worktree_latest.json").write_text(
        json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    OPS_DIR.joinpath("active_shared_worktree_latest.md").write_text(
        _render_markdown(marker),
        encoding="utf-8",
    )

    print("=== Active Shared Worktree Published ===")
    print(f"active_shared_root: {ACTIVE_SHARED_ROOT}")
    print(f"branch: {marker['branch']}")
    print(f"head: {marker['head']}")
    print(f"marker: {ACTIVE_SHARED_ROOT / 'ACTIVE_SHARED_WORKTREE.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
