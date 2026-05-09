#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 52-C — CLI summary поверх :class:`AuditAnalyzer`.

Usage:
    python3 scripts/krab_audit_summary.py [--window 60] [--json]

Read-only: только читает логи bash_guard и agent_audit, печатает агрегаты
и обнаруженные suspicious-patterns. НЕ ходит в сеть, НЕ пишет файлы.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _ensure_repo_on_path() -> None:
    """Гарантируем, что src/ доступен при прямом запуске из scripts/."""
    here = Path(__file__).resolve().parent.parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


def _format_text(report: dict) -> str:
    lines: list[str] = []
    win = report.get("window_minutes", 60)
    lines.append(f"# Krab Audit Summary — last {win} minutes")
    lines.append("")

    bash = report.get("bash_audit") or {}
    lines.append("## Bash audit (krab_bash_audit.log)")
    lines.append(f"  total:         {bash.get('total_events', 0)}")
    lines.append(f"  ALLOW:         {bash.get('allow', 0)}")
    lines.append(f"  NEEDS_CONFIRM: {bash.get('needs_confirm', 0)}")
    lines.append(f"  BLOCK:         {bash.get('block', 0)}")
    top_block = bash.get("top_blocked_patterns") or []
    if top_block:
        lines.append("  top blocked reasons:")
        for row in top_block:
            lines.append(f"    - {row.get('reason', '')!r}: {row.get('count', 0)}")
    top_conf = bash.get("top_confirmed_patterns") or []
    if top_conf:
        lines.append("  top confirm reasons:")
        for row in top_conf:
            lines.append(f"    - {row.get('reason', '')!r}: {row.get('count', 0)}")
    lines.append("")

    agent = report.get("agent_audit") or {}
    lines.append("## Agent audit (agent_audit.jsonl)")
    lines.append(f"  total:              {agent.get('total_events', 0)}")
    lines.append(f"  first_time_blocks:  {agent.get('first_time_blocks', 0)}")
    by_ch = agent.get("by_channel") or {}
    if by_ch:
        lines.append("  by channel:")
        for ch, cnt in sorted(by_ch.items(), key=lambda x: -x[1]):
            lines.append(f"    - {ch}: {cnt}")
    by_act = agent.get("by_action") or {}
    if by_act:
        lines.append("  by action:")
        for act, cnt in sorted(by_act.items(), key=lambda x: -x[1]):
            lines.append(f"    - {act}: {cnt}")
    lines.append("")

    alerts = report.get("alerts") or []
    lines.append(f"## Alerts ({len(alerts)})")
    if not alerts:
        lines.append("  (none)")
    for al in alerts:
        sev = al.get("severity", "info").upper()
        kind = al.get("kind", "?")
        det = al.get("details", "")
        lines.append(f"  [{sev}] {kind}: {det}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_on_path()
    from src.core.agent_audit_analyzer import AuditAnalyzer

    parser = argparse.ArgumentParser(description="Krab audit log analyzer (Wave 52-C)")
    parser.add_argument(
        "--window",
        type=int,
        default=60,
        help="Окно анализа в минутах (default: 60)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести raw JSON отчёт (для pipe в jq).",
    )
    args = parser.parse_args(argv)

    analyzer = AuditAnalyzer()
    report = analyzer.analyze_recent(window_minutes=args.window)

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(_format_text(report) + "\n")
    # Non-zero exit code если есть warning-уровневые alerts (для CI / hooks).
    has_warning = any(
        (a.get("severity") or "").lower() == "warning" for a in (report.get("alerts") or [])
    )
    return 1 if has_warning else 0


if __name__ == "__main__":
    raise SystemExit(main())
