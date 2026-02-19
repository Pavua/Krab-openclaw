# -*- coding: utf-8 -*-
"""
Генератор checkpoint-отчёта для безопасного перехода в новый чат.

Зачем:
1. Минимизировать риск потери контекста при ошибке 413;
2. Давать короткий, воспроизводимый срез состояния проекта;
3. Ускорить handoff между диалогами и между coding-приложениями.

Связь с проектом:
- Используется вместе с docs/codex_context_hygiene.md;
- Поддерживает параллельную разработку через разные ветки/worktree.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import subprocess
import sys
from typing import List


ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "artifacts" / "context_checkpoints"


def _run(cmd: List[str]) -> str:
    """Безопасный запуск shell-команды с коротким timeout."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        payload = (proc.stdout or "").strip()
        if payload:
            return payload
        return (proc.stderr or "").strip()
    except Exception as exc:
        return f"<error: {exc}>"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now().astimezone()
    ts = now.strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"checkpoint_{ts}.md"

    branch = _run(["git", "branch", "--show-current"])
    head = _run(["git", "rev-parse", "--short", "HEAD"])
    status = _run(["git", "status", "--short"])
    recent_commits = _run(["git", "log", "--oneline", "-n", "8"])

    changed_count = 0
    changed_lines = []
    if status and not status.startswith("<error:"):
        changed_lines = [line for line in status.splitlines() if line.strip()]
        changed_count = len(changed_lines)

    openclaw_status = _run(["sh", "-lc", "command -v openclaw >/dev/null 2>&1 && openclaw status || echo 'openclaw_cli_not_found'"])
    overlap_check = _run([sys.executable, "scripts/check_workstream_overlap.py"])

    md = [
        "# Checkpoint для нового чата",
        "",
        f"- Время: `{now.isoformat()}`",
        f"- Ветка: `{branch or 'unknown'}`",
        f"- HEAD: `{head or 'unknown'}`",
        f"- Изменённых файлов: `{changed_count}`",
        "",
        "## Что сделано",
        "1. [заполни кратко] Ключевые изменения за текущий этап.",
        "2. [заполни кратко] Какие проверки уже запускались и их итог.",
        "",
        "## Что осталось",
        "1. [заполни кратко] Следующий конкретный шаг.",
        "2. [заполни кратко] Риски/блокеры.",
        "",
        "## Git status (--short)",
        "```text",
        status or "(чисто)",
        "```",
        "",
        "## Последние коммиты",
        "```text",
        recent_commits or "(нет данных)",
        "```",
        "",
        "## OpenClaw status",
        "```text",
        openclaw_status or "(нет данных)",
        "```",
        "",
        "## Workstream overlap check",
        "```text",
        overlap_check or "(нет данных)",
        "```",
        "",
        "## Paste в новый чат (шаблон)",
        "```text",
        "[CHECKPOINT]",
        f"branch={branch or 'unknown'}",
        f"head={head or 'unknown'}",
        f"changed_files={changed_count}",
        "scope=[что именно делаем дальше]",
        "done=[что уже готово и проверено]",
        "next=[ближайший шаг]",
        "risks=[кратко]",
        "```",
        "",
    ]

    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"✅ Checkpoint создан: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
