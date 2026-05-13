# -*- coding: utf-8 -*-
"""
Тесты Wave 172: backup retention sweep.

Проверяют scripts/krab_backup_retention_sweep.py:
- три цели подметаются независимо
- keep_recent сохраняет N свежих
- max_age_days защищает молодые файлы
- dry_run не мутирует
- отсутствующая папка — graceful (не падаем)
- name_filter для files (нерелевантные не трогаются)
- dated_backup_dirs трогает только YYYY-MM-DD каталоги
- env overrides работают
- bytes_freed считается корректно
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from pathlib import Path

import pytest

# Импорт через путь, потому что скрипт лежит в scripts/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import krab_backup_retention_sweep as sweep_mod  # noqa: E402

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_file(path: Path, *, size: int = 1024, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _make_dir(path: Path, *, file_sizes: list[int] | None = None, mtime: float | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if file_sizes:
        for i, size in enumerate(file_sizes):
            _make_file(path / f"f{i}.dat", size=size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _ts_days_ago(days: float) -> float:
    return time.time() - days * 86400


def _fake_home(tmp_path: Path) -> Path:
    """Возвращает tmp_path в роли $HOME и заранее создаёт скелет .openclaw."""
    (tmp_path / ".openclaw").mkdir()
    return tmp_path


# ── unit: build_default_targets ────────────────────────────────────────────────


class TestBuildDefaultTargets:
    def test_four_targets(self, tmp_path: Path) -> None:
        # Wave 191: добавлена 4-я цель openclaw_config_backups.
        targets = sweep_mod.build_default_targets(home=tmp_path)
        names = [t.name for t in targets]
        assert names == [
            "krab_memory_backups",
            "workspace_tarballs",
            "dated_backup_dirs",
            "openclaw_config_backups",
        ]

    def test_default_policies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Снимаем env-переменные если установлены
        monkeypatch.delenv("KRAB_BACKUP_RETENTION_KEEP_RECENT", raising=False)
        monkeypatch.delenv("KRAB_BACKUP_RETENTION_MAX_AGE_DAYS", raising=False)
        targets = sweep_mod.build_default_targets(home=tmp_path)

        by_name = {t.name: t for t in targets}
        assert by_name["krab_memory_backups"].keep_recent == 3
        # Wave 191: harmonized с остальными — 14 дней (раньше было 7).
        assert by_name["krab_memory_backups"].max_age_days == 14
        assert by_name["workspace_tarballs"].max_age_days == 14
        assert by_name["dated_backup_dirs"].max_age_days == 14
        assert by_name["openclaw_config_backups"].max_age_days == 14
        assert by_name["openclaw_config_backups"].keep_recent == 3

    def test_env_override_keep_recent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KRAB_BACKUP_RETENTION_KEEP_RECENT", "5")
        monkeypatch.delenv("KRAB_BACKUP_RETENTION_MAX_AGE_DAYS", raising=False)
        targets = sweep_mod.build_default_targets(home=tmp_path)
        for t in targets:
            assert t.keep_recent == 5

    def test_env_override_max_age_overrides_db_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Если задан env, то даже DB snapshots используют общее значение.
        monkeypatch.setenv("KRAB_BACKUP_RETENTION_MAX_AGE_DAYS", "30")
        targets = sweep_mod.build_default_targets(home=tmp_path)
        for t in targets:
            assert t.max_age_days == 30

    def test_env_invalid_falls_back_to_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KRAB_BACKUP_RETENTION_KEEP_RECENT", "not-a-number")
        targets = sweep_mod.build_default_targets(home=tmp_path)
        # Должен подхватить default=3
        for t in targets:
            assert t.keep_recent == 3


# ── unit: sweep_target / files ─────────────────────────────────────────────────


class TestSweepTargetFiles:
    def test_missing_dir_is_graceful(self, tmp_path: Path) -> None:
        target = sweep_mod.RetentionTarget(
            name="t",
            path=tmp_path / "nope",
            keep_recent=3,
            max_age_days=7,
            entry_kind="file",
        )
        report = sweep_mod.sweep_target(target, dry_run=False)
        assert report.exists is False
        assert report.removed == []
        assert report.kept == []
        assert report.bytes_freed == 0
        assert report.error is None

    def test_keeps_top_n_removes_older_rest(self, tmp_path: Path) -> None:
        """5 файлов всех возрастом 30д — keep_recent=2 → 2 живых, 3 удалены."""
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        # 5 файлов, mtime убывает (i=0 — самый свежий, i=4 — самый старый), все старые.
        for i in range(5):
            _make_file(
                target_dir / f"archive-{i}.db",
                size=1000,
                mtime=_ts_days_ago(30 + i),  # все > 14d
            )

        target = sweep_mod.RetentionTarget(
            name="dbs",
            path=target_dir,
            keep_recent=2,
            max_age_days=14,
            entry_kind="file",
            name_filter=lambda n: n.startswith("archive-") and n.endswith(".db"),
        )
        report = sweep_mod.sweep_target(target, dry_run=False)

        assert len(report.kept) == 2
        assert len(report.removed) == 3
        assert report.bytes_freed == 3000

        # Проверяем что свежие 2 файла остались
        remaining = sorted(p.name for p in target_dir.iterdir())
        assert remaining == ["archive-0.db", "archive-1.db"]

    def test_young_files_always_kept(self, tmp_path: Path) -> None:
        """Файлы моложе max_age_days не удаляются, даже за пределами top-N."""
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        # 5 свежих файлов (1 день назад)
        for i in range(5):
            _make_file(
                target_dir / f"archive-{i}.db",
                size=500,
                mtime=_ts_days_ago(1),
            )

        target = sweep_mod.RetentionTarget(
            name="dbs",
            path=target_dir,
            keep_recent=2,
            max_age_days=7,
            entry_kind="file",
            name_filter=lambda n: n.endswith(".db"),
        )
        report = sweep_mod.sweep_target(target, dry_run=False)

        # Все 5 остаются благодаря max_age_days защите
        assert len(report.kept) == 5
        assert len(report.removed) == 0
        assert report.bytes_freed == 0

    def test_name_filter_skips_unrelated(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        # archive-* — релевантные (старые → должны удалиться)
        for i in range(4):
            _make_file(target_dir / f"archive-{i}.db", size=100, mtime=_ts_days_ago(30 + i))
        # README — нерелевантный, должен остаться нетронутым
        readme = _make_file(target_dir / "README.md", size=50, mtime=_ts_days_ago(30))

        target = sweep_mod.RetentionTarget(
            name="dbs",
            path=target_dir,
            keep_recent=1,
            max_age_days=7,
            entry_kind="file",
            name_filter=lambda n: n.startswith("archive-") and n.endswith(".db"),
        )
        report = sweep_mod.sweep_target(target, dry_run=False)

        # 4 archive-* → 1 keep, 3 remove. README не трогаем.
        assert len(report.removed) == 3
        assert readme.exists(), "README не должен подметаться"

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        for i in range(5):
            _make_file(target_dir / f"archive-{i}.db", size=100, mtime=_ts_days_ago(30 + i))

        target = sweep_mod.RetentionTarget(
            name="dbs",
            path=target_dir,
            keep_recent=1,
            max_age_days=7,
            entry_kind="file",
            name_filter=lambda n: n.endswith(".db"),
        )
        report = sweep_mod.sweep_target(target, dry_run=True)

        # В отчёте 4 файла "удалены", но физически все 5 живы.
        assert len(report.removed) == 4
        assert report.dry_run is True
        assert report.bytes_freed == 400
        remaining = sorted(p.name for p in target_dir.iterdir())
        assert remaining == [f"archive-{i}.db" for i in range(5)]


# ── unit: sweep_target / dirs ──────────────────────────────────────────────────


class TestSweepTargetDirs:
    def test_only_iso_date_dirs_swept(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        # 4 dated каталога (старые)
        dated = []
        for i, d in enumerate(["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-04"]):
            p = _make_dir(target_dir / d, file_sizes=[500], mtime=_ts_days_ago(30 + i))
            dated.append(p)

        # Каталог "workspace" — НЕ date-pattern, должен остаться нетронутым.
        ws = _make_dir(target_dir / "workspace", file_sizes=[5000], mtime=_ts_days_ago(60))
        # Обычный файл — игнорим (entry_kind="dir")
        loose_file = _make_file(target_dir / "stray.txt", size=10)

        target = sweep_mod.RetentionTarget(
            name="dated",
            path=target_dir,
            keep_recent=1,
            max_age_days=7,
            entry_kind="dir",
        )
        report = sweep_mod.sweep_target(target, dry_run=False)

        assert len(report.removed) == 3  # 4 dated minus keep_recent=1
        assert ws.exists(), "workspace/ не трогаем (имя не YYYY-MM-DD)"
        assert loose_file.exists(), "обычные файлы при kind='dir' игнорим"

    def test_dir_size_aggregated(self, tmp_path: Path) -> None:
        target_dir = tmp_path / "backups"
        target_dir.mkdir()

        # Старый каталог с 3 файлами по 1000 байт.
        old_dir = _make_dir(
            target_dir / "2026-01-01",
            file_sizes=[1000, 1000, 1000],
            mtime=_ts_days_ago(60),
        )
        # Свежий каталог-якорь — оставляем чтобы keep_recent не съел старый.
        _make_dir(target_dir / "2026-05-01", file_sizes=[100], mtime=_ts_days_ago(1))

        target = sweep_mod.RetentionTarget(
            name="dated",
            path=target_dir,
            keep_recent=1,
            max_age_days=7,
            entry_kind="dir",
        )
        report = sweep_mod.sweep_target(target, dry_run=False)

        assert len(report.removed) == 1
        # bytes_freed должен совпасть с суммой файлов в old_dir
        assert report.bytes_freed == 3000
        assert not old_dir.exists()


# ── integration: run_sweep + default targets ──────────────────────────────────


class TestRunSweepEndToEnd:
    def test_all_four_targets(self, tmp_path: Path) -> None:
        home = _fake_home(tmp_path)
        openclaw = home / ".openclaw"

        # 1. krab_memory/backups: 5 archive-* старых → 3 keep, 2 remove
        mem_dir = openclaw / "krab_memory" / "backups"
        mem_dir.mkdir(parents=True)
        for i in range(5):
            _make_file(
                mem_dir / f"archive-2026050{i}.db",
                size=200,
                mtime=_ts_days_ago(20 + i),
            )

        # 2. backups/workspace: 5 workspace_*.tar.gz старых → 3 keep, 2 remove
        ws_dir = openclaw / "backups" / "workspace"
        ws_dir.mkdir(parents=True)
        for i in range(5):
            _make_file(
                ws_dir / f"workspace_2026050{i}_040005.tar.gz",
                size=300,
                mtime=_ts_days_ago(20 + i),
            )

        # 3. backups/YYYY-MM-DD: 5 dated dirs старых → 3 keep, 2 remove
        for i in range(5):
            d = openclaw / "backups" / f"2026-04-{15 + i:02d}"
            _make_dir(d, file_sizes=[400], mtime=_ts_days_ago(20 + i))

        # 4. Wave 191: openclaw config bak-файлы в корне .openclaw/
        #    5 файлов старых → 3 keep, 2 remove.
        for i in range(5):
            _make_file(
                openclaw / f"openclaw.json.bak_2026050{i}_010101",
                size=100,
                mtime=_ts_days_ago(20 + i),
            )
        # Активный openclaw.json НЕ должен попасть в подметание (нет суффикса
        # .bak* — но защита от опечатки в name_filter тоже есть).
        active = _make_file(openclaw / "openclaw.json", size=999, mtime=_ts_days_ago(1))

        targets = sweep_mod.build_default_targets(home=home)
        summary = sweep_mod.run_sweep(targets=targets, dry_run=False)

        assert summary["total_removed"] == 8  # 2 per target × 4 targets
        by_name = {t["name"]: t for t in summary["targets"]}
        assert by_name["krab_memory_backups"]["removed_count"] == 2
        assert by_name["workspace_tarballs"]["removed_count"] == 2
        assert by_name["dated_backup_dirs"]["removed_count"] == 2
        assert by_name["openclaw_config_backups"]["removed_count"] == 2

        # bytes_freed сумма (включая 4-ю цель: 100×2)
        assert summary["total_bytes_freed"] == (200 * 2) + (300 * 2) + (400 * 2) + (100 * 2)

        # Активный openclaw.json — нетронут.
        assert active.exists()

    def test_dry_run_summary(self, tmp_path: Path) -> None:
        home = _fake_home(tmp_path)
        mem_dir = home / ".openclaw" / "krab_memory" / "backups"
        mem_dir.mkdir(parents=True)
        for i in range(5):
            _make_file(
                mem_dir / f"archive-2026050{i}.db",
                size=100,
                mtime=_ts_days_ago(20 + i),
            )

        targets = sweep_mod.build_default_targets(home=home)
        summary = sweep_mod.run_sweep(targets=targets, dry_run=True)

        assert summary["dry_run"] is True
        # Файлы НЕ удалены физически
        assert len(list(mem_dir.iterdir())) == 5

    def test_missing_dirs_graceful(self, tmp_path: Path) -> None:
        """Если backup-папки отсутствуют — sweep успешно завершается без ошибок."""
        home = _fake_home(tmp_path)  # только пустой .openclaw
        targets = sweep_mod.build_default_targets(home=home)
        summary = sweep_mod.run_sweep(targets=targets, dry_run=False)

        assert summary["total_removed"] == 0
        assert summary["total_bytes_freed"] == 0
        # Первые 3 цели — отдельные папки, их нет.
        # 4-я цель (openclaw_config_backups) — это сам ~/.openclaw, он СОЗДАН
        # _fake_home, но пустой → exists=True, но 0 совпадений по name_filter.
        by_name = {t["name"]: t for t in summary["targets"]}
        assert by_name["krab_memory_backups"]["exists"] is False
        assert by_name["workspace_tarballs"]["exists"] is False
        assert by_name["dated_backup_dirs"]["exists"] is False
        assert by_name["openclaw_config_backups"]["exists"] is True
        for t in summary["targets"]:
            assert t["error"] is None
            assert t["removed_count"] == 0


# ── Wave 191: openclaw_config_backups 4-я цель ────────────────────────────────


class TestOpenclawConfigBackups:
    """Тесты для 4-й цели sweeper'а — openclaw config bak-файлов."""

    def _build_target(self, openclaw_dir: Path) -> sweep_mod.RetentionTarget:
        """Достаёт 4-ю цель из дефолтной конфигурации."""
        home = openclaw_dir.parent
        targets = sweep_mod.build_default_targets(home=home)
        return next(t for t in targets if t.name == "openclaw_config_backups")

    def test_matches_bak_variants(self, tmp_path: Path) -> None:
        """name_filter должен ловить все варианты openclaw.json.bak* + openclaw.backup_*.json."""
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()

        target = self._build_target(openclaw)
        f = target.name_filter
        assert f is not None

        # Должны матчиться:
        assert f("openclaw.json.bak") is True
        assert f("openclaw.json.bak.1") is True
        assert f("openclaw.json.bak-1777760013") is True
        assert f("openclaw.json.bak-nightly-20260506-0300") is True
        assert f("openclaw.json.bak_20260302_003107") is True
        assert f("openclaw.json.bak_session27_1777247341") is True
        assert f("openclaw.json.bak_webui_runtime_20260422_170824Z") is True
        assert f("openclaw.backup_20260513_012039.json") is True
        assert f("openclaw.backup_T_20260513_013245.json") is True

        # НЕ должны матчиться (защищаем активный конфиг и посторонние файлы):
        assert f("openclaw.json") is False
        assert f("openclaw.db") is False
        assert f("config.json") is False
        assert f("gateway.log") is False
        assert f("openclaw copy.json") is False  # копия от пользователя, не бэкап

    def test_old_bak_files_removed(self, tmp_path: Path) -> None:
        """Старые bak-файлы (> max_age_days) подметаются, top-N свежих остаются."""
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()

        # 6 старых .bak файлов + 2 свежих
        for i in range(6):
            _make_file(
                openclaw / f"openclaw.json.bak_2026020{i}_010101",
                size=50,
                mtime=_ts_days_ago(30 + i),
            )
        for i in range(2):
            _make_file(
                openclaw / f"openclaw.json.bak_2026051{i}_010101",
                size=50,
                mtime=_ts_days_ago(1 + i),
            )

        # Активный конфиг + случайный файл — не трогаются.
        active = _make_file(openclaw / "openclaw.json", size=999)
        gateway_log = _make_file(openclaw / "gateway.log", size=100)

        target = self._build_target(openclaw)
        report = sweep_mod.sweep_target(target, dry_run=False)

        # keep_recent=3, max_age_days=14:
        # 2 свежих (1d, 2d) — kept by age. 1 старый (30d) — kept by top-N.
        # 5 старых (31d..35d) — удалены.
        assert len(report.removed) == 5
        assert active.exists()
        assert gateway_log.exists()

    def test_backup_json_variant(self, tmp_path: Path) -> None:
        """Файлы openclaw.backup_*.json подметаются по другой ветке filter."""
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()

        for i in range(5):
            _make_file(
                openclaw / f"openclaw.backup_2026050{i}_010101.json",
                size=200,
                mtime=_ts_days_ago(20 + i),
            )

        target = self._build_target(openclaw)
        report = sweep_mod.sweep_target(target, dry_run=False)

        # 5 файлов, все старые → keep top-3, remove 2.
        assert len(report.kept) == 3
        assert len(report.removed) == 2
        assert report.bytes_freed == 400  # 200 × 2

    def test_active_openclaw_json_never_swept(self, tmp_path: Path) -> None:
        """Активный openclaw.json НИКОГДА не подметается, даже если он старый."""
        openclaw = tmp_path / ".openclaw"
        openclaw.mkdir()

        # Очень старый активный конфиг
        active = _make_file(openclaw / "openclaw.json", size=500, mtime=_ts_days_ago(365))

        target = self._build_target(openclaw)
        report = sweep_mod.sweep_target(target, dry_run=False)

        # name_filter отверг → активный конфиг даже не попадает в kept/removed.
        assert active.exists()
        assert all("openclaw.json" not in r["path"] or ".bak" in r["path"]
                   for r in report.removed)


# ── CLI smoke ──────────────────────────────────────────────────────────────────


class TestCLI:
    def test_main_dry_run_returns_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() с --dry-run в пустом окружении возвращает 0."""
        monkeypatch.setattr(sweep_mod.Path, "home", classmethod(lambda cls: tmp_path))
        # Импорт уже выполнен — но build_default_targets() вызывает Path.home()
        # внутри main → перехватим через monkeypatch выше.
        rc = sweep_mod.main(["--dry-run", "--json"])
        assert rc == 0
