#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 29-A: Auto-refresh CLAUDE.md autotables.

Перегенерирует:
  - docs/CLAUDE_AUTO_ENDPOINTS.md  (live count + table из /api/endpoints)
  - docs/CLAUDE_AUTO_HANDLERS.md   (grep async def handle_* функций)
  - docs/CLAUDE_AUTO_PROMETHEUS.md (alerts + metrics из rules/*.yml)
  - CLAUDE.md счётчики в строках вида "X routes", "X handle_* функций" и т.д.

Использование:
  python scripts/refresh_claude_md_autotables.py [--dry-run] [--no-commit]

ENV:
  KRAB_PANEL_URL=http://127.0.0.1:8080   (по умолчанию)
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
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Пути
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
DOCS_DIR = ROOT / "docs"

# Prometheus rules — ищем в deploy/monitoring/rules/ и ops/prometheus/
_RULES_DIRS = [
    ROOT / "deploy" / "monitoring" / "rules",
    ROOT / "ops" / "prometheus",
]


# ---------------------------------------------------------------------------
# 1. Fetch endpoints из Owner Panel
# ---------------------------------------------------------------------------


def fetch_endpoints() -> list[dict]:
    """Запрашивает /api/endpoints — возвращает список {path, method}.

    При сетевой ошибке выводит предупреждение и возвращает пустой список.
    """
    base_url = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
    url = base_url.rstrip("/") + "/api/endpoints"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        endpoints = data.get("endpoints", [])
        return endpoints
    except urllib.error.URLError as exc:
        print(f"⚠️  Не удалось подключиться к Owner Panel ({url}): {exc}")
        return []
    except (json.JSONDecodeError, KeyError) as exc:
        print(f"⚠️  Неожиданный формат ответа /api/endpoints: {exc}")
        return []


# ---------------------------------------------------------------------------
# 2. Grep handle_* функций из handlers
# ---------------------------------------------------------------------------


def grep_handlers() -> list[str]:
    """Собирает все async def handle_* из src/handlers/.

    Ищет в src/handlers/commands/*.py и src/handlers/command_handlers.py.
    Возвращает отсортированный список имён функций без дублей.
    """
    search_paths: list[Path] = []

    commands_dir = ROOT / "src" / "handlers" / "commands"
    if commands_dir.is_dir():
        search_paths.extend(sorted(commands_dir.glob("*.py")))

    main_handlers = ROOT / "src" / "handlers" / "command_handlers.py"
    if main_handlers.exists():
        search_paths.append(main_handlers)

    handlers: set[str] = set()
    pattern = re.compile(r"^async def (handle_\w+)\(")

    for path in search_paths:
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                m = pattern.match(line)
                if m:
                    handlers.add(m.group(1))
        except OSError as exc:
            print(f"⚠️  Не удалось прочитать {path}: {exc}")

    return sorted(handlers)


# ---------------------------------------------------------------------------
# 3. Парсинг Prometheus rules
# ---------------------------------------------------------------------------


def parse_prometheus_rules() -> tuple[list[str], list[str]]:
    """Читает *.yml из prometheus rules directories.

    Возвращает (alerts, metrics) — уникальные отсортированные списки.
    """
    alerts: set[str] = set()
    metrics: set[str] = set()

    for rules_dir in _RULES_DIRS:
        if not rules_dir.is_dir():
            continue
        for rules_file in sorted(rules_dir.glob("*.yml")):
            try:
                text = rules_file.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                print(f"⚠️  Не удалось прочитать {rules_file}: {exc}")
                continue
            # Парсим имена alert-правил
            for m in re.finditer(r"^\s*- alert:\s*(\w+)", text, re.M):
                alerts.add(m.group(1))
            # Парсим имена метрик krab_*
            for m in re.finditer(r"\bkrab_[a-z_]+", text):
                metrics.add(m.group(0))

    return sorted(alerts), sorted(metrics)


# ---------------------------------------------------------------------------
# 4. Запись docs/CLAUDE_AUTO_ENDPOINTS.md
# ---------------------------------------------------------------------------


def write_endpoints_doc(endpoints: list[dict], session_tag: str) -> int:
    """Перезаписывает docs/CLAUDE_AUTO_ENDPOINTS.md.

    Возвращает количество маршрутов.
    """
    # Сортируем по пути, группируем одинаковые пути с несколькими методами
    rows: list[str] = []
    for ep in sorted(endpoints, key=lambda e: e.get("path", "")):
        path = ep.get("path", "?")
        method = ep.get("method", "?")
        rows.append(f"| `{path}` | {method} |")

    count = len(endpoints)
    content = (
        f"# Auto-generated endpoints ({count} routes)\n"
        "\n"
        "**29 routers** в `src/modules/web_routers/` через factory `build_X_router(ctx)` pattern.\n"
        f"Обновлено: {session_tag}. Live проверить: `GET /api/endpoints`\n"
        "\n"
        "| Endpoint | Метод |\n"
        "|----------|-------|\n"
        + "\n".join(rows)
        + "\n"
    )

    target = DOCS_DIR / "CLAUDE_AUTO_ENDPOINTS.md"
    target.write_text(content, encoding="utf-8")
    print(f"✓ Записан {target} ({count} маршрутов)")
    return count


# ---------------------------------------------------------------------------
# 5. Запись docs/CLAUDE_AUTO_HANDLERS.md
# ---------------------------------------------------------------------------


def write_handlers_doc(handlers: list[str], session_tag: str) -> int:
    """Перезаписывает docs/CLAUDE_AUTO_HANDLERS.md.

    Возвращает количество найденных handle_* функций.
    """
    count = len(handlers)

    # Форматируем в строку вида !имя_без_handle_
    items = "\n".join(f"`!{h.removeprefix('handle_')}`" for h in handlers)

    content = (
        f"# Auto-generated handlers ({count} handle_* функций)\n"
        "\n"
        "Phase 2 Waves 1-18 + Session 35-38. Модули в `src/handlers/commands/` (24 файла):\n"
        "text_utils / chat / scheduler / voice / memory / social / ai / swarm / translator"
        " / system / admin / cli / fileio / group_admin / content / state / observability"
        " / memory_admin / policy / _shared / engine_commands / curator_commands"
        " + `src/handlers/command_handlers.py`\n"
        "\n"
        f"Обновлено: {session_tag}. Актуальный счётчик:\n"
        "```bash\n"
        "grep -hE \"^async def handle_\" src/handlers/commands/*.py"
        " src/handlers/command_handlers.py | sort -u | wc -l\n"
        "```\n"
        "\n"
        + items
        + "\n"
    )

    target = DOCS_DIR / "CLAUDE_AUTO_HANDLERS.md"
    target.write_text(content, encoding="utf-8")
    print(f"✓ Записан {target} ({count} обработчиков)")
    return count


# ---------------------------------------------------------------------------
# 6. Запись docs/CLAUDE_AUTO_PROMETHEUS.md
# ---------------------------------------------------------------------------


def write_prometheus_doc(alerts: list[str], metrics: list[str], session_tag: str) -> None:
    """Перезаписывает docs/CLAUDE_AUTO_PROMETHEUS.md."""
    n_alerts = len(alerts)
    n_metrics = len(metrics)

    # Форматируем alerts в строку через запятую (совместимо со старым форматом)
    alerts_line = ", ".join(f"`{a}`" for a in alerts) if alerts else "(нет)"
    metrics_lines = "\n".join(f"`{m}`," for m in metrics) if metrics else "(нет)"

    content = (
        f"# Auto-generated Prometheus ({n_alerts} алертов, {n_metrics} метрик)\n"
        "\n"
        f"Обновлено: {session_tag}. Конфиг: `scripts/prometheus/`\n"
        "\n"
        f"## Alerts ({n_alerts})\n"
        "\n"
        + alerts_line
        + "\n\n"
        f"## Metrics ({n_metrics})\n"
        "\n"
        + metrics_lines
        + "\n"
    )

    target = DOCS_DIR / "CLAUDE_AUTO_PROMETHEUS.md"
    target.write_text(content, encoding="utf-8")
    print(f"✓ Записан {target} ({n_alerts} alerts, {n_metrics} metrics)")


# ---------------------------------------------------------------------------
# 7. Обновление счётчиков в CLAUDE.md
# ---------------------------------------------------------------------------


def update_claude_md_counts(n_endpoints: int, n_handlers: int) -> None:
    """Обновляет числовые счётчики в CLAUDE.md через regex-замену.

    Заменяет только конкретные паттерны, не трогая остальной текст.
    """
    text = CLAUDE_MD.read_text(encoding="utf-8")
    original = text

    # "(~X routes)" / "(X routes; ...)" в auto-generated комментарии
    # Паттерн учитывает как "(~248 routes)" так и "(~248 routes; **29 routers**...)"
    text = re.sub(r"\(~?\d+\s+routes\)", f"(~{n_endpoints} routes)", text)
    text = re.sub(r"\(~?\d+\s+routes;", f"(~{n_endpoints} routes;", text)

    # "X handle_* функций" — в строке "### Auto-generated handlers (...)"
    text = re.sub(
        r"(\d+)\s+handle_\*\s+функций",
        f"{n_handlers} handle_* функций",
        text,
    )

    # "(~X функций)" — короткие упоминания
    text = re.sub(r"\(~?\d+\s+функций\)", f"(~{n_handlers} функций)", text)

    if text != original:
        CLAUDE_MD.write_text(text, encoding="utf-8")
        print("✓ CLAUDE.md счётчики обновлены")
    else:
        print("· CLAUDE.md счётчики не изменились (значения совпадают)")


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------


def build_session_tag() -> str:
    """Возвращает тег вида 'auto-refresh (05.05.2026)'."""
    today = datetime.now().strftime("%d.%m.%Y")
    return f"auto-refresh ({today})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Wave 29-A: обновить auto-generated tables в CLAUDE.md и docs/."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только вывести статистику, не записывать файлы.",
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Не создавать git-коммит после записи.",
    )
    args = parser.parse_args(argv)

    # --- Сбор данных ---
    endpoints = fetch_endpoints()
    handlers = grep_handlers()
    alerts, metrics = parse_prometheus_rules()

    n_endp = len(endpoints)
    n_hand = len(handlers)
    n_alerts = len(alerts)
    n_metrics = len(metrics)

    print(f"endpoints : {n_endp}")
    print(f"handlers  : {n_hand}")
    print(f"alerts    : {n_alerts}")
    print(f"metrics   : {n_metrics}")

    if args.dry_run:
        print("(dry-run: файлы не записаны)")
        return 0

    # --- Запись файлов ---
    session_tag = build_session_tag()
    write_endpoints_doc(endpoints, session_tag)
    write_handlers_doc(handlers, session_tag)
    write_prometheus_doc(alerts, metrics, session_tag)
    update_claude_md_counts(n_endp, n_hand)

    if not args.no_commit:
        changed_files = [
            "CLAUDE.md",
            "docs/CLAUDE_AUTO_ENDPOINTS.md",
            "docs/CLAUDE_AUTO_HANDLERS.md",
            "docs/CLAUDE_AUTO_PROMETHEUS.md",
        ]
        subprocess.run(
            ["git", "add", "--"] + changed_files,
            cwd=str(ROOT),
            check=False,
        )
        result = subprocess.run(
            ["git", "commit", "-m", "docs(auto): refresh CLAUDE.md autotables"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("✓ git commit создан")
        elif "nothing to commit" in result.stdout + result.stderr:
            print("· Нет изменений для коммита")
        else:
            print(f"⚠️  git commit вернул код {result.returncode}: {result.stderr.strip()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
