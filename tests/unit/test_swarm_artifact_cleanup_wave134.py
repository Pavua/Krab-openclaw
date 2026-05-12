# -*- coding: utf-8 -*-
"""
Wave 134: тесты для weekly swarm artifact retention sweep.

Проверяем discover_artifacts, run_cleanup (keep last N per team, dry-run,
freed_mb accounting), LaunchAgent plist (Sun 06:00) и Prometheus
set_swarm_artifacts_metrics gauge snapshot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts import krab_swarm_artifact_cleanup as cleanup
from src.core import prometheus_metrics


def _touch(path: Path, *, mtime: float, content: str = "{}") -> Path:
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _make_team_artifacts(
    base: Path, team: str, count: int, *, start_ts: int = 1_700_000_000
) -> list[Path]:
    base.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(count):
        ts = start_ts + i
        p = base / f"{team}_{ts}.json"
        out.append(_touch(p, mtime=float(ts), content=json.dumps({"team": team})))
    return out


# ---- 1: discover ---------------------------------------------------------


def test_discover_groups_by_team_and_sorts_desc(tmp_path: Path) -> None:
    _make_team_artifacts(tmp_path, "coders", 3, start_ts=1000)
    _make_team_artifacts(tmp_path, "traders", 2, start_ts=2000)
    # «Чужой» файл — должен игнорироваться.
    _touch(tmp_path / "stray.json", mtime=1.0, content="{}")
    _touch(tmp_path / "report.md", mtime=1.0, content="x")

    grouped = cleanup.discover_artifacts(tmp_path)
    assert set(grouped.keys()) == {"coders", "traders"}
    assert len(grouped["coders"]) == 3
    # DESC по mtime — самый свежий первым.
    assert grouped["coders"][0].stat().st_mtime > grouped["coders"][-1].stat().st_mtime


def test_discover_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert cleanup.discover_artifacts(tmp_path / "absent") == {}


# ---- 2: cleanup keep last N ---------------------------------------------


def test_run_cleanup_keeps_last_n_per_team(tmp_path: Path) -> None:
    _make_team_artifacts(tmp_path, "coders", 5, start_ts=1000)
    _make_team_artifacts(tmp_path, "traders", 2, start_ts=2000)

    report = cleanup.run_cleanup(base_dir=tmp_path, keep_per_team=3, now_fn=lambda: 9000.0)

    assert report["total_before"] == 7
    assert report["deleted"] == 2  # coders had 5 → keep 3, delete 2
    assert report["kept_per_team"] == {"coders": 3, "traders": 2}
    assert report["deleted_by_team"] == {"coders": 2}
    # Реальные файлы удалены: самые старые coders (ts 1000, 1001).
    remaining_coders = sorted(
        f.name for f in tmp_path.glob("coders_*.json")
    )
    assert remaining_coders == ["coders_1002.json", "coders_1003.json", "coders_1004.json"]


# ---- 3: dry-run ----------------------------------------------------------


def test_run_cleanup_dry_run_does_not_delete(tmp_path: Path) -> None:
    files = _make_team_artifacts(tmp_path, "analysts", 4, start_ts=1000)
    report = cleanup.run_cleanup(base_dir=tmp_path, keep_per_team=2, dry_run=True)

    assert report["dry_run"] is True
    assert report["deleted"] == 2
    # Всё на диске сохранилось.
    for f in files:
        assert f.exists()


# ---- 4: empty / under-limit ---------------------------------------------


def test_run_cleanup_under_limit_noop(tmp_path: Path) -> None:
    _make_team_artifacts(tmp_path, "creative", 5, start_ts=1000)
    report = cleanup.run_cleanup(base_dir=tmp_path, keep_per_team=200)
    assert report["deleted"] == 0
    assert report["kept_per_team"] == {"creative": 5}
    assert report["deleted_by_team"] == {}


def test_run_cleanup_empty_dir(tmp_path: Path) -> None:
    report = cleanup.run_cleanup(base_dir=tmp_path / "absent", keep_per_team=200)
    assert report["total_before"] == 0
    assert report["deleted"] == 0
    assert report["kept_per_team"] == {}


# ---- 5: freed_mb accounting ---------------------------------------------


def test_run_cleanup_reports_freed_mb(tmp_path: Path) -> None:
    base = tmp_path
    base.mkdir(parents=True, exist_ok=True)
    # 4 файла по 1024 байта; keep=1 → delete 3 → ~3 KB freed.
    payload = "x" * 1024
    for i in range(4):
        p = base / f"coders_{1000 + i}.json"
        _touch(p, mtime=float(1000 + i), content=payload)

    report = cleanup.run_cleanup(base_dir=base, keep_per_team=1)
    assert report["deleted"] == 3
    # freed_mb округлено до 3 знаков; 3 KB == 0.00293 → округление даёт 0.003.
    assert report["freed_mb"] == pytest.approx(3 * 1024 / (1024 * 1024), abs=0.001)
    assert report["freed_mb"] > 0


# ---- 6: Prometheus snapshot ---------------------------------------------


def test_set_swarm_artifacts_metrics_updates_snapshot() -> None:
    prometheus_metrics._SWARM_ARTIFACTS_TOTAL.clear()
    prometheus_metrics._SWARM_ARTIFACTS_SIZE_MB[0] = 0.0

    prometheus_metrics.set_swarm_artifacts_metrics(
        {"coders": 3, "traders": 7}, 12.5
    )
    assert prometheus_metrics._SWARM_ARTIFACTS_TOTAL == {"coders": 3, "traders": 7}
    assert prometheus_metrics._SWARM_ARTIFACTS_SIZE_MB[0] == 12.5

    # Повторный вызов перезаписывает (а не аккумулирует) snapshot.
    prometheus_metrics.set_swarm_artifacts_metrics({"analysts": 1}, 0.5)
    assert prometheus_metrics._SWARM_ARTIFACTS_TOTAL == {"analysts": 1}
    assert prometheus_metrics._SWARM_ARTIFACTS_SIZE_MB[0] == 0.5


# ---- 7: LaunchAgent plist sanity ----------------------------------------


def test_launchagent_plist_present_and_sunday_06h() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    plist = repo_root / "scripts" / "launchagents" / "ai.krab.swarm-artifact-cleanup.plist"
    assert plist.exists()
    raw = plist.read_text(encoding="utf-8")
    assert "ai.krab.swarm-artifact-cleanup" in raw
    assert "krab_swarm_artifact_cleanup.py" in raw
    # Weekday 0 = Sunday в launchd, Hour 6.
    assert "<key>Weekday</key>" in raw
    assert "<integer>0</integer>" in raw
    assert "<key>Hour</key>" in raw
    assert "<integer>6</integer>" in raw
    assert "KRAB_SWARM_ARTIFACT_KEEP" in raw
