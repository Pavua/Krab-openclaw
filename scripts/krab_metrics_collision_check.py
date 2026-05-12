#!/usr/bin/env python3
"""Wave 141: Prometheus metric name collision detector.

Сканирует `src/core/metrics/*.py`, парсит AST, извлекает имена всех
Prometheus-метрик (Counter/Gauge/Histogram/Summary), строит карту
{metric_name: [submodule, ...]} и подсвечивает коллизии (count > 1).

Учитывает алиасы импорта вида:
    from prometheus_client import Gauge as _GaugeRL
    import prometheus_client as pc; pc.Counter(...)

Usage:
    python scripts/krab_metrics_collision_check.py
    python scripts/krab_metrics_collision_check.py --json
    python scripts/krab_metrics_collision_check.py --strict   # exit 1 при коллизии

Exit codes:
    0 — нет коллизий (или non-strict)
    1 — коллизия + --strict
    2 — IO / parse error
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# Prometheus client primitives, которые мы трекаем.
_METRIC_TYPES = {"Counter", "Gauge", "Histogram", "Summary"}

# Дефолтный путь отчёта в runtime-state.
_DEFAULT_REPORT = (
    Path.home() / ".openclaw" / "krab_runtime_state" / "metrics_audit.json"
)


def _resolve_metrics_pkg(repo_root: Path) -> Path:
    """Возвращает путь к пакету src/core/metrics."""
    pkg = repo_root / "src" / "core" / "metrics"
    if not pkg.is_dir():
        raise FileNotFoundError(f"metrics package not found at {pkg}")
    return pkg


def _collect_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Возвращает (direct_names, module_aliases) для prometheus_client.

    direct_names — имена, к которым через `from prometheus_client import X as Y`
                   привязан один из METRIC_TYPES (например `_GaugeRL`).
    module_aliases — алиасы модуля (`import prometheus_client as pc` → {"pc"}).
    """
    direct: set[str] = set()
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "prometheus_client":
            for alias in node.names:
                if alias.name in _METRIC_TYPES:
                    direct.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "prometheus_client":
                    module_aliases.add(alias.asname or alias.name)
    return direct, module_aliases


def _extract_metric_names(
    tree: ast.AST,
    direct_names: set[str],
    module_aliases: set[str],
) -> list[tuple[str, int]]:
    """Извлекает [(metric_name, lineno), ...] вызовы Prometheus-конструкторов."""
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        # Прямой вызов: Counter("name", ...) или _GaugeRL("name", ...)
        is_metric_call = False
        if isinstance(func, ast.Name) and func.id in direct_names:
            is_metric_call = True
        # Атрибутный вызов: pc.Counter("name", ...)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr in _METRIC_TYPES
            and isinstance(func.value, ast.Name)
            and func.value.id in module_aliases
        ):
            is_metric_call = True

        if not is_metric_call:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((first.value, node.lineno))
    return found


def _scan_file(path: Path) -> list[tuple[str, int]]:
    """Парсит один файл, возвращает список (metric_name, lineno)."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise RuntimeError(f"syntax error in {path}: {exc}") from exc
    direct, module_aliases = _collect_aliases(tree)
    if not direct and not module_aliases:
        return []
    return _extract_metric_names(tree, direct, module_aliases)


def audit_metrics_package(pkg_dir: Path) -> dict[str, object]:
    """Сканирует все .py в пакете, возвращает структурированный отчёт."""
    name_to_locations: dict[str, list[dict[str, object]]] = defaultdict(list)
    files_scanned: list[str] = []

    for path in sorted(pkg_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        files_scanned.append(path.name)
        for metric_name, lineno in _scan_file(path):
            name_to_locations[metric_name].append(
                {"submodule": path.stem, "lineno": lineno}
            )

    collisions = {
        name: locations
        for name, locations in name_to_locations.items()
        if len({loc["submodule"] for loc in locations}) > 1
    }

    return {
        "wave": 141,
        "files_scanned": files_scanned,
        "total_metrics": len(name_to_locations),
        "total_definitions": sum(len(v) for v in name_to_locations.values()),
        "collisions_count": len(collisions),
        "collisions": collisions,
        "all_metrics": {
            name: [loc["submodule"] for loc in locs]
            for name, locs in sorted(name_to_locations.items())
        },
    }


def write_report(report: dict[str, object], output: Path) -> None:
    """Записывает JSON отчёт; создаёт parents при необходимости."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _format_human(report: dict[str, object]) -> str:
    lines: list[str] = []
    lines.append(f"Wave 141 metrics audit — {report['total_metrics']} unique names")
    lines.append(f"  files scanned: {len(report['files_scanned'])}")
    lines.append(f"  total definitions: {report['total_definitions']}")
    lines.append(f"  collisions: {report['collisions_count']}")
    if report["collisions"]:
        lines.append("  --- COLLISIONS ---")
        for name, locs in report["collisions"].items():  # type: ignore[union-attr]
            joined = ", ".join(
                f"{loc['submodule']}:{loc['lineno']}" for loc in locs
            )
            lines.append(f"    {name}: {joined}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wave 141: metric collision audit")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repo root (default: parent of scripts/)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_REPORT,
        help=f"JSON output path (default: {_DEFAULT_REPORT})",
    )
    parser.add_argument("--json", action="store_true", help="печать JSON в stdout")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 при наличии коллизий",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        pkg = _resolve_metrics_pkg(args.repo)
        report = audit_metrics_package(pkg)
        write_report(report, args.output)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        print(f"metrics audit failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(_format_human(report))
        print(f"report → {args.output}")

    if args.strict and report["collisions_count"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
