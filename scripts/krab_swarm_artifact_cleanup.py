#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 134: weekly swarm artifact retention policy.

Чистит `~/.openclaw/krab_runtime_state/swarm_artifacts/<team>_<ts>.json`,
оставляя последние N артефактов на команду (env `KRAB_SWARM_ARTIFACT_KEEP`,
default 200). Запускается из LaunchAgent ai.krab.swarm-artifact-cleanup
каждое воскресенье 06:00 локально.

Возвращает JSON-отчёт + публикует Prometheus gauges:
- krab_swarm_artifacts_total{team} — артефактов после cleanup;
- krab_swarm_artifacts_size_mb — общий размер на диске после cleanup.

Exit 0 — успех (включая «нечего чистить»). Exit 1 — ошибка.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

# Имя файла: <team>_<unix_ts>.json. Команда — всё до последнего "_<digits>".
_FILENAME_RE = re.compile(r"^(?P<team>.+)_(?P<ts>\d+)\.json$")

_DEFAULT_DIR = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_artifacts"
_DEFAULT_KEEP = int(os.environ.get("KRAB_SWARM_ARTIFACT_KEEP", "200"))


def _parse_team(filename: str) -> str | None:
    """Извлекает имя команды из имени файла артефакта."""
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    return m.group("team")


def discover_artifacts(base_dir: Path) -> dict[str, list[Path]]:
    """Группирует артефакты по team. Файлы внутри — sorted by mtime DESC (новые первые)."""
    if not base_dir.exists():
        return {}
    by_team: dict[str, list[Path]] = defaultdict(list)
    for f in base_dir.glob("*.json"):
        if not f.is_file():
            continue
        team = _parse_team(f.name)
        if team is None:
            continue
        by_team[team].append(f)
    for team in by_team:
        by_team[team].sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dict(by_team)


def _total_size_bytes(base_dir: Path) -> int:
    """Сумма размеров всех *.json в директории. 0 если директории нет."""
    if not base_dir.exists():
        return 0
    total = 0
    for f in base_dir.glob("*.json"):
        try:
            total += f.stat().st_size
        except OSError:
            continue
    return total


def run_cleanup(
    *,
    base_dir: Path | None = None,
    keep_per_team: int = _DEFAULT_KEEP,
    dry_run: bool = False,
    now_fn: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Выполняет retention sweep. Возвращает JSON-совместимый отчёт."""
    base = base_dir or _DEFAULT_DIR
    now = (now_fn or time.time)()
    keep = max(0, int(keep_per_team))

    grouped = discover_artifacts(base)
    total_before = sum(len(v) for v in grouped.values())
    size_before = _total_size_bytes(base)

    deleted = 0
    deleted_by_team: dict[str, int] = {}
    kept_per_team: dict[str, int] = {}
    freed_bytes = 0

    for team, files in grouped.items():
        if len(files) <= keep:
            kept_per_team[team] = len(files)
            continue
        survivors = files[:keep]
        to_remove = files[keep:]
        kept_per_team[team] = len(survivors)
        team_deleted = 0
        for victim in to_remove:
            try:
                size = victim.stat().st_size
            except OSError:
                size = 0
            if dry_run:
                team_deleted += 1
                freed_bytes += size
                continue
            try:
                victim.unlink()
                team_deleted += 1
                freed_bytes += size
            except OSError:
                continue
        deleted += team_deleted
        if team_deleted:
            deleted_by_team[team] = team_deleted

    size_after = size_before - freed_bytes if dry_run else _total_size_bytes(base)

    report = {
        "timestamp": int(now),
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "base_dir": str(base),
        "keep_per_team": keep,
        "dry_run": bool(dry_run),
        "total_before": total_before,
        "total_after": sum(kept_per_team.values()),
        "deleted": deleted,
        "deleted_by_team": deleted_by_team,
        "kept_per_team": kept_per_team,
        "freed_mb": round(freed_bytes / (1024 * 1024), 3),
        "size_mb_after": round(size_after / (1024 * 1024), 3),
    }
    return report


def emit_prometheus(report: dict[str, Any]) -> None:
    """Публикует gauges: per-team total + общий size_mb. Best-effort, не бросает."""
    try:
        from src.core import prometheus_metrics  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    kept = report.get("kept_per_team") or {}
    size_mb = float(report.get("size_mb_after") or 0.0)
    setter = getattr(prometheus_metrics, "set_swarm_artifacts_metrics", None)
    if callable(setter):
        try:
            setter(kept, size_mb)
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    base_env = os.environ.get("KRAB_SWARM_ARTIFACT_DIR")
    base_dir = Path(base_env) if base_env else _DEFAULT_DIR
    keep = int(os.environ.get("KRAB_SWARM_ARTIFACT_KEEP", str(_DEFAULT_KEEP)))
    dry = os.environ.get("KRAB_SWARM_ARTIFACT_DRY_RUN", "").lower() in ("1", "true", "yes")

    try:
        report = run_cleanup(base_dir=base_dir, keep_per_team=keep, dry_run=dry)
    except Exception as exc:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    emit_prometheus(report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
