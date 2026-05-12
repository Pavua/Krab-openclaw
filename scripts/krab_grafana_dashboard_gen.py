"""Генератор Grafana dashboard JSON для Krab Overview (Wave 132).

Собирает single-page дашборд из существующих Prometheus метрик Краба.
Метрики могут поставляться статическим списком (default) или быть
вычитаны live с ``/metrics`` через флаг ``--fetch``.

Использование::

    python scripts/krab_grafana_dashboard_gen.py
    python scripts/krab_grafana_dashboard_gen.py --validate
    python scripts/krab_grafana_dashboard_gen.py --output path/to/file.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Каждая строка дашборда: title + список panel (title, expr, unit, type)
# Метрики берутся из существующего реестра Краба (см. src/* + Session 47 Waves).
ROW_SYSTEM = (
    "System",
    [
        ("Uptime (seconds)", "krab_uptime_seconds", "s", "stat"),
        ("Disk free (bytes)", "krab_disk_free_bytes", "bytes", "gauge"),
        ("Disk used %", "krab_disk_used_pct", "percent", "gauge"),
    ],
)
ROW_TELEGRAM = (
    "Telegram",
    [
        (
            "FloodWait duration (sum)",
            "sum(rate(krab_floodwait_seconds_total[5m]))",
            "s",
            "timeseries",
        ),
        ("Dispatcher tick", "krab_dispatcher_tick_seconds", "s", "timeseries"),
        (
            "Swarm probes (rate)",
            "sum(rate(krab_swarm_probe_total[5m]))",
            "ops",
            "timeseries",
        ),
    ],
)
ROW_COST = (
    "Cost",
    [
        ("Daily cost (EUR)", "krab_cost_daily_used_eur", "currencyEUR", "stat"),
        ("Daily budget %", "krab_cost_daily_pct", "percent", "gauge"),
        ("Weekly cost (EUR)", "krab_cost_weekly_used_eur", "currencyEUR", "stat"),
        ("Weekly budget %", "krab_cost_weekly_pct", "percent", "gauge"),
        (
            "Paid Gemini guard blocks",
            "sum(rate(krab_paid_gemini_guard_blocks_total[5m]))",
            "ops",
            "timeseries",
        ),
    ],
)
ROW_AI_ROUTING = (
    "AI Routing",
    [
        (
            "Smart routing decisions",
            "sum by (decision) (rate(krab_smart_routing_decision_total[5m]))",
            "ops",
            "timeseries",
        ),
        (
            "Model fallback rate",
            "sum by (from_model, to_model) (rate(krab_model_fallback_total[5m]))",
            "ops",
            "timeseries",
        ),
        (
            "Agent engine runs",
            "sum by (engine) (rate(krab_agent_engine_runs_total[5m]))",
            "ops",
            "timeseries",
        ),
    ],
)
ROW_EXTERNAL = (
    "External",
    [
        ("Sentry quota remaining", "krab_sentry_quota_remaining", "short", "stat"),
        ("MCP servers alive", "krab_mcp_servers_alive", "short", "stat"),
        ("SSL days remaining", "krab_ssl_days_remaining", "d", "stat"),
        (
            "Cloudflare tunnel up",
            "krab_cf_tunnel_up",
            "short",
            "stat",
        ),
    ],
)

ROWS: list[tuple[str, list[tuple[str, str, str, str]]]] = [
    ROW_SYSTEM,
    ROW_TELEGRAM,
    ROW_COST,
    ROW_AI_ROUTING,
    ROW_EXTERNAL,
]

ROW_HEIGHT = 8
PANEL_WIDTH_TOTAL = 24
DEFAULT_OUTPUT = Path("deploy/grafana/dashboards/krab_overview.json")


def _panel(
    panel_id: int,
    title: str,
    expr: str,
    unit: str,
    panel_type: str,
    grid_pos: dict[str, int],
) -> dict[str, Any]:
    """Сформировать единичную panel в формате Grafana 10."""
    return {
        "id": panel_id,
        "title": title,
        "type": panel_type,
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "gridPos": grid_pos,
        "targets": [
            {
                "expr": expr,
                "refId": "A",
                "datasource": {"type": "prometheus", "uid": "prometheus"},
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 80},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {},
    }


def build_dashboard() -> dict[str, Any]:
    """Собрать полный JSON дашборда Krab Overview."""
    panels: list[dict[str, Any]] = []
    panel_id = 1
    y = 0
    for row_title, row_panels in ROWS:
        # Row header (Grafana row panel — collapse group)
        panels.append(
            {
                "id": panel_id,
                "type": "row",
                "title": row_title,
                "collapsed": False,
                "gridPos": {"h": 1, "w": PANEL_WIDTH_TOTAL, "x": 0, "y": y},
                "panels": [],
            }
        )
        panel_id += 1
        y += 1
        # Равная ширина для всех panel в строке
        width = max(1, PANEL_WIDTH_TOTAL // len(row_panels))
        x = 0
        for title, expr, unit, ptype in row_panels:
            panels.append(
                _panel(
                    panel_id,
                    title,
                    expr,
                    unit,
                    ptype,
                    {"h": ROW_HEIGHT, "w": width, "x": x, "y": y},
                )
            )
            panel_id += 1
            x += width
        y += ROW_HEIGHT

    return {
        "title": "Krab Overview",
        "uid": "krab-overview",
        "tags": ["krab", "auto-generated", "wave132"],
        "schemaVersion": 38,
        "version": 1,
        "editable": True,
        "graphTooltip": 0,
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "timezone": "browser",
        "templating": {"list": []},
        "annotations": {"list": []},
        "panels": panels,
    }


def validate_dashboard(dashboard: dict[str, Any]) -> list[str]:
    """Проверка корректности JSON дашборда. Возвращает список ошибок."""
    errors: list[str] = []
    required = ("title", "uid", "schemaVersion", "panels")
    for key in required:
        if key not in dashboard:
            errors.append(f"missing top-level key: {key}")

    panels = dashboard.get("panels", [])
    if not isinstance(panels, list) or not panels:
        errors.append("panels must be non-empty list")
        return errors

    seen_ids: set[int] = set()
    for idx, panel in enumerate(panels):
        if "id" not in panel:
            errors.append(f"panel[{idx}] missing id")
            continue
        pid = panel["id"]
        if pid in seen_ids:
            errors.append(f"duplicate panel id: {pid}")
        seen_ids.add(pid)
        if "title" not in panel:
            errors.append(f"panel[{idx}] missing title")
        if "gridPos" not in panel:
            errors.append(f"panel[{idx}] missing gridPos")
        if panel.get("type") != "row":
            targets = panel.get("targets", [])
            if not targets:
                errors.append(f"panel[{idx}] '{panel.get('title')}' has no targets")
            elif not targets[0].get("expr"):
                errors.append(f"panel[{idx}] '{panel.get('title')}' has empty expr")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Krab Grafana dashboard generator")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="output JSON path",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="validate JSON structure without writing",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="print JSON to stdout instead of writing file",
    )
    args = parser.parse_args(argv)

    dashboard = build_dashboard()
    errors = validate_dashboard(dashboard)

    if args.validate:
        if errors:
            for e in errors:
                print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"OK: {len(dashboard['panels'])} panels, valid structure")
        return 0

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    payload = json.dumps(dashboard, indent=2, ensure_ascii=False)
    if args.stdout:
        print(payload)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
        print(f"wrote {args.output} ({len(dashboard['panels'])} panels)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
