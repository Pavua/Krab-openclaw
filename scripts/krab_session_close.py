#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 87: автоматизированное закрытие сессии Krab.

Генерирует `.remember/next_session.md` handoff-шаблон + сводку:
  - commits с момента предыдущей сессии (--since-commit / KRAB_SESSION_PREV_HEAD)
  - total LOC delta (git diff --stat)
  - текущее количество тестов (pytest --collect-only)
  - количество API endpoints (live curl, optional)
  - количество Prometheus alerts (grep krab_alerts.yml)
  - заготовка pending-items (TODO grep по CLAUDE.md)

Использование:
  python scripts/krab_session_close.py [--since-commit <sha>] [--out <path>] [--dry-run]

ENV:
  KRAB_SESSION_PREV_HEAD  — fallback SHA если --since-commit не передан
  KRAB_PANEL_URL=http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / ".remember" / "next_session.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
ALERTS_FILE = ROOT / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"


# ---------------------------------------------------------------------------
# Git операции
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path = ROOT) -> str:
    """Запускает git и возвращает stdout. При ошибке — пустая строка + warning."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0:
            print(f"⚠️  git {' '.join(args)} → rc={result.returncode}: {result.stderr.strip()}")
            return ""
        return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"⚠️  git {' '.join(args)} failed: {exc}")
        return ""


def resolve_prev_head(cli_value: str | None) -> str | None:
    """Резолвит предыдущий HEAD: CLI > ENV > None.

    При None caller должен gracefully degrade (показать "(не задан)").
    """
    if cli_value:
        return cli_value.strip() or None
    env = os.environ.get("KRAB_SESSION_PREV_HEAD")
    if env:
        return env.strip() or None
    return None


def collect_commits(since: str) -> list[str]:
    """`git log --oneline <since>..HEAD` → список строк."""
    out = _run_git(["log", "--oneline", f"{since}..HEAD"])
    return [line for line in out.splitlines() if line.strip()]


def collect_diff_stat(since: str) -> str:
    """Последняя строка `git diff --stat <since>..HEAD` (summary)."""
    out = _run_git(["diff", "--stat", f"{since}..HEAD"])
    lines = [line for line in out.splitlines() if line.strip()]
    if not lines:
        return "(нет изменений)"
    return lines[-1].strip()


def current_head() -> str:
    """Короткий SHA текущего HEAD."""
    return _run_git(["rev-parse", "--short", "HEAD"]).strip() or "(unknown)"


def current_branch() -> str:
    """Текущая ветка."""
    return _run_git(["branch", "--show-current"]).strip() or "(detached)"


# ---------------------------------------------------------------------------
# Тесты, endpoints, alerts
# ---------------------------------------------------------------------------


def count_tests() -> int:
    """`pytest --collect-only -q` → парсит "N tests collected" в хвосте.

    Возвращает -1 при ошибке.
    """
    venv_python = ROOT / "venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    try:
        result = subprocess.run(
            [python, "-m", "pytest", "--collect-only", "-q"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"⚠️  pytest collect failed: {exc}")
        return -1

    # Парсим "12702 tests collected" или "12702/12810 tests collected"
    output = result.stdout + result.stderr
    match = re.search(r"(\d+)(?:/\d+)?\s+tests?\s+collected", output)
    if match:
        return int(match.group(1))
    return -1


def count_endpoints() -> int:
    """Live запрос к Owner Panel /api/endpoints. -1 при недоступности."""
    base_url = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
    url = base_url.rstrip("/") + "/api/endpoints"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return len(data.get("endpoints", []))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        print(f"⚠️  Owner Panel недоступна ({url}): {exc}")
        return -1


def count_alerts() -> int:
    """grep `^      - alert:` в krab_alerts.yml. -1 если файл отсутствует."""
    if not ALERTS_FILE.exists():
        print(f"⚠️  {ALERTS_FILE} не найден")
        return -1
    try:
        text = ALERTS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"⚠️  Не удалось прочитать {ALERTS_FILE}: {exc}")
        return -1
    return len(re.findall(r"^\s*- alert:", text, re.M))


# ---------------------------------------------------------------------------
# Pending items — поиск TODO/FIXME в CLAUDE.md
# ---------------------------------------------------------------------------


def extract_pending_items() -> list[str]:
    """Извлекает TODO/FIXME/P0/P1 заголовки из CLAUDE.md.

    Простая эвристика: строки начинающиеся с "### P0", "### P1", "TODO:", "FIXME:".
    """
    if not CLAUDE_MD.exists():
        return []
    try:
        text = CLAUDE_MD.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    items: list[str] = []
    pattern = re.compile(r"^(?:###?\s+(?:P[012].+)|.*\b(?:TODO|FIXME):\s*.+)$", re.M)
    for match in pattern.finditer(text):
        line = match.group(0).strip()
        if line and line not in items:
            items.append(line)
    return items[:10]  # cap to 10 чтобы не раздувать handoff


# ---------------------------------------------------------------------------
# Генерация handoff
# ---------------------------------------------------------------------------


def build_handoff(
    *,
    prev_head: str | None,
    cur_head: str,
    branch: str,
    commits: list[str],
    diff_stat: str,
    n_tests: int,
    n_endpoints: int,
    n_alerts: int,
    pending: list[str],
    session_tag: str,
) -> str:
    """Собирает Markdown handoff для следующей сессии."""
    prev_display = prev_head or "(не задан — передай --since-commit или KRAB_SESSION_PREV_HEAD)"
    n_commits = len(commits)
    tests_display = str(n_tests) if n_tests >= 0 else "(не удалось собрать)"
    endpoints_display = str(n_endpoints) if n_endpoints >= 0 else "(Owner Panel offline)"
    alerts_display = str(n_alerts) if n_alerts >= 0 else "(rules file missing)"

    commits_block = "\n".join(f"- `{line}`" for line in commits) if commits else "_(нет коммитов)_"
    pending_block = (
        "\n".join(f"- {item}" for item in pending)
        if pending
        else "_(пусто — пройди CLAUDE.md backlog)_"
    )

    return f"""# Session Handoff — auto-generated {session_tag}

> Сгенерировано `scripts/krab_session_close.py`. Отредактируй вручную: добавь wave-narratives, P0/P1 priorities, edge cases.

## TL;DR

- **prev HEAD**: `{prev_display}`
- **current HEAD**: `{cur_head}` ({branch})
- **Коммитов**: {n_commits}
- **Diff stat**: {diff_stat}
- **Тестов**: {tests_display}
- **Endpoints (live)**: {endpoints_display}
- **Prometheus alerts**: {alerts_display}

## Recent commits ({n_commits})

{commits_block}

## Pending items (heuristic — TODO/FIXME/P[012] из CLAUDE.md)

{pending_block}

## Quick commands

```bash
# Restart Krab (с user approval!)
bash "/Users/pablito/Antigravity_AGENTS/new Stop Krab.command" && sleep 3 && \\
  bash "/Users/pablito/Antigravity_AGENTS/new start_krab.command"

# Verify live
curl -sS http://127.0.0.1:8080/api/health/lite
curl -sS http://127.0.0.1:8080/api/runtime/status

# Тесты
cd {ROOT}
venv/bin/python -m pytest tests/unit/ -q --tb=line
venv/bin/ruff check src/

# Этот скрипт (regenerate)
venv/bin/python scripts/krab_session_close.py --since-commit {prev_display} --dry-run
```

## Подсказки

- Если `prev HEAD` не задан — экспортни `KRAB_SESSION_PREV_HEAD=<sha>` ИЛИ передай `--since-commit <sha>`.
- Owner Panel offline → запусти Krab (start_krab.command), панель поднимается за 5-10s.
- Тесты не собрались → проверь `venv/bin/python` exists и pytest install.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate end-of-session handoff for Krab.")
    parser.add_argument(
        "--since-commit",
        dest="since_commit",
        default=None,
        help="SHA предыдущей сессии. Fallback: ENV KRAB_SESSION_PREV_HEAD.",
    )
    parser.add_argument(
        "--out",
        dest="out",
        default=str(DEFAULT_OUT),
        help=f"Путь для handoff (default: {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Печатает в stdout, не пишет файл.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    prev = resolve_prev_head(args.since_commit)
    cur = current_head()
    branch = current_branch()
    session_tag = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if prev:
        commits = collect_commits(prev)
        diff_stat = collect_diff_stat(prev)
    else:
        commits = []
        diff_stat = "(prev HEAD не задан)"

    n_tests = count_tests()
    n_endpoints = count_endpoints()
    n_alerts = count_alerts()
    pending = extract_pending_items()

    handoff = build_handoff(
        prev_head=prev,
        cur_head=cur,
        branch=branch,
        commits=commits,
        diff_stat=diff_stat,
        n_tests=n_tests,
        n_endpoints=n_endpoints,
        n_alerts=n_alerts,
        pending=pending,
        session_tag=session_tag,
    )

    # Summary в stdout всегда
    print(f"=== krab_session_close summary ({session_tag}) ===")
    print(f"prev HEAD     : {prev or '(не задан)'}")
    print(f"current HEAD  : {cur} ({branch})")
    print(f"commits       : {len(commits)}")
    print(f"diff stat     : {diff_stat}")
    print(f"tests         : {n_tests if n_tests >= 0 else '(N/A)'}")
    print(f"endpoints     : {n_endpoints if n_endpoints >= 0 else '(N/A)'}")
    print(f"alerts        : {n_alerts if n_alerts >= 0 else '(N/A)'}")
    print(f"pending hints : {len(pending)}")

    out_path = Path(args.out)
    if args.dry_run:
        print("\n=== DRY RUN — handoff не записан ===\n")
        print(handoff)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(handoff, encoding="utf-8")
    print(f"\n✓ Handoff записан: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
