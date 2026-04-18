#!/usr/bin/env python3
"""Composite docs synchroniser для Krab.

Сканирует исходники и перегенерирует marker-секции в CLAUDE.md:
    - Owner Panel endpoints (из src/modules/web_app.py)
    - Команды userbot (из src/handlers/command_handlers.py)
    - Prometheus алерты / метрики (из ops/prometheus/krab_alerts.yml)

Поддерживает ``--dry-run`` — печатает diff без записи на диск.
Используется как Wave 20-G composite-генератор.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

# Корень проекта = на уровень выше scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent
WEB_APP = REPO_ROOT / "src" / "modules" / "web_app.py"
CMD_HANDLERS = REPO_ROOT / "src" / "handlers" / "command_handlers.py"
PROM_RULES = REPO_ROOT / "ops" / "prometheus" / "krab_alerts.yml"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Маркеры секций в CLAUDE.md (агент не обязательно их встречает — делаем append).
MARK_ENDPOINTS_BEGIN = "<!-- BEGIN:auto-endpoints -->"
MARK_ENDPOINTS_END = "<!-- END:auto-endpoints -->"
MARK_COMMANDS_BEGIN = "<!-- BEGIN:auto-commands -->"
MARK_COMMANDS_END = "<!-- END:auto-commands -->"
MARK_METRICS_BEGIN = "<!-- BEGIN:auto-metrics -->"
MARK_METRICS_END = "<!-- END:auto-metrics -->"

# Regex для @self.app.<method>("/path", ...)
ROUTE_RE = re.compile(
    r'@self\.app\.(get|post|put|delete|patch)\(\s*["\']([^"\']+)["\']'
)
# Regex для async def handle_<name>(...)
HANDLER_RE = re.compile(r"async def handle_([a-z_0-9]+)\s*\(")
# Regex для alert: <Name> в prometheus yaml
ALERT_RE = re.compile(r"^\s*-\s*alert:\s*(\S+)", re.MULTILINE)
# Regex для krab_* metric names (слово начинается с krab_)
METRIC_RE = re.compile(r"\bkrab_[a-z0-9_]+\b")


def extract_endpoints(text: str) -> list[tuple[str, str]]:
    """Возвращает уникальные (method, path), отсортированные по path."""
    seen: dict[str, str] = {}
    for match in ROUTE_RE.finditer(text):
        method = match.group(1).upper()
        path = match.group(2)
        if path.startswith("/api") or path in {"/", "/metrics"}:
            prev = seen.get(path)
            seen[path] = f"{prev}/{method}" if prev and method not in prev else (prev or method)
    return sorted(seen.items(), key=lambda kv: kv[0])


def extract_commands(text: str) -> list[str]:
    """Возвращает отсортированный список имён handle_<cmd>."""
    names = {match.group(1) for match in HANDLER_RE.finditer(text)}
    return sorted(names)


def extract_alerts_and_metrics(text: str) -> tuple[list[str], list[str]]:
    alerts = sorted(set(ALERT_RE.findall(text)))
    metrics = sorted(set(METRIC_RE.findall(text)))
    return alerts, metrics


def render_endpoints(endpoints: list[tuple[str, str]]) -> str:
    lines = [
        MARK_ENDPOINTS_BEGIN,
        "",
        f"### Auto-generated endpoints table ({len(endpoints)} маршрутов)",
        "",
        "| Endpoint | Метод |",
        "|----------|-------|",
    ]
    for path, method in endpoints:
        lines.append(f"| `{path}` | {method} |")
    lines.extend(["", MARK_ENDPOINTS_END])
    return "\n".join(lines)


def render_commands(commands: list[str]) -> str:
    lines = [
        MARK_COMMANDS_BEGIN,
        "",
        f"### Auto-generated handlers ({len(commands)} команд)",
        "",
    ]
    # Компактно по 6 в ряд.
    row: list[str] = []
    for name in commands:
        row.append(f"`!{name}`")
        if len(row) == 6:
            lines.append(", ".join(row))
            row = []
    if row:
        lines.append(", ".join(row))
    lines.extend(["", MARK_COMMANDS_END])
    return "\n".join(lines)


def render_metrics(alerts: list[str], metrics: list[str]) -> str:
    lines = [
        MARK_METRICS_BEGIN,
        "",
        f"### Auto-generated Prometheus ({len(alerts)} алертов, {len(metrics)} метрик)",
        "",
        "Alerts: " + ", ".join(f"`{a}`" for a in alerts) if alerts else "Alerts: —",
        "",
        "Metrics: " + ", ".join(f"`{m}`" for m in metrics) if metrics else "Metrics: —",
        "",
        MARK_METRICS_END,
    ]
    return "\n".join(lines)


def replace_or_append(doc: str, begin: str, end: str, block: str) -> str:
    """Заменяет существующий маркированный блок или дописывает в конец."""
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(doc):
        return pattern.sub(block, doc)
    if not doc.endswith("\n"):
        doc += "\n"
    return doc + "\n" + block + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync auto-generated sections in CLAUDE.md")
    parser.add_argument("--dry-run", action="store_true", help="Не писать файл, показать diff")
    args = parser.parse_args()

    web_text = WEB_APP.read_text(encoding="utf-8") if WEB_APP.exists() else ""
    cmd_text = CMD_HANDLERS.read_text(encoding="utf-8") if CMD_HANDLERS.exists() else ""
    prom_text = PROM_RULES.read_text(encoding="utf-8") if PROM_RULES.exists() else ""

    endpoints = extract_endpoints(web_text)
    commands = extract_commands(cmd_text)
    alerts, metrics = extract_alerts_and_metrics(prom_text)

    print(f"[sync_docs] endpoints={len(endpoints)} commands={len(commands)} "
          f"alerts={len(alerts)} metrics={len(metrics)}")

    if not CLAUDE_MD.exists():
        print(f"[sync_docs] CLAUDE.md not found at {CLAUDE_MD}", file=sys.stderr)
        return 1

    original = CLAUDE_MD.read_text(encoding="utf-8")
    updated = original
    updated = replace_or_append(
        updated, MARK_ENDPOINTS_BEGIN, MARK_ENDPOINTS_END, render_endpoints(endpoints)
    )
    updated = replace_or_append(
        updated, MARK_COMMANDS_BEGIN, MARK_COMMANDS_END, render_commands(commands)
    )
    updated = replace_or_append(
        updated, MARK_METRICS_BEGIN, MARK_METRICS_END, render_metrics(alerts, metrics)
    )

    if updated == original:
        print("[sync_docs] no changes")
        return 0

    diff = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile="CLAUDE.md (before)",
            tofile="CLAUDE.md (after)",
            n=1,
        )
    )
    changed_lines = sum(1 for line in diff if line.startswith(("+", "-")) and not line.startswith(("+++", "---")))
    print(f"[sync_docs] diff: {changed_lines} lines changed")

    if args.dry_run:
        sys.stdout.writelines(diff[:80])
        if len(diff) > 80:
            print(f"... ({len(diff) - 80} more diff lines)")
        print("[sync_docs] dry-run: not writing")
        return 0

    CLAUDE_MD.write_text(updated, encoding="utf-8")
    print(f"[sync_docs] CLAUDE.md updated ({changed_lines} lines changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
