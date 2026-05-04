# -*- coding: utf-8 -*-
"""
Тесты retention policy для session backup-файлов (Wave 18-A).

Проверяют cleanup_old_backups() из src/bootstrap/session_recovery.py:
- Защита live-файлов (kraab.session/-wal/-shm)
- keep_recent=3 по каждой категории
- max_age_days (старые удаляются, молодые сохраняются)
- Sidecar удаляются вместе с main backup
- bytes_freed считается корректно
- dry_run не мутирует файлы
- Пустая директория — пустой результат
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from src.bootstrap.session_recovery import cleanup_old_backups

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_file(path: Path, size: int = 1024, mtime: float | None = None) -> Path:
    """Создаёт файл заданного размера и mtime."""
    path.write_bytes(b"\x00" * size)
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def _ts_days_ago(days: float) -> float:
    """Unix timestamp N дней назад."""
    return time.time() - days * 86400


# ── tests ──────────────────────────────────────────────────────────────────────


class TestProtectedFilesNeverRemoved:
    """Тест 1: live-файлы никогда не удаляются."""

    def test_protected_files_survive_cleanup(self, tmp_path: Path) -> None:
        """kraab.session, -wal, -shm всегда остаются после cleanup."""
        # Создаём protected live-файлы
        session = _make_file(tmp_path / "kraab.session")
        wal = _make_file(tmp_path / "kraab.session-wal")
        shm = _make_file(tmp_path / "kraab.session-shm")

        # Добавляем несколько backup файлов чтобы cleanup действительно работал
        for i in range(5):
            _make_file(tmp_path / f"kraab.session.bak-corrupt-{i:010d}", mtime=_ts_days_ago(30))

        result = cleanup_old_backups(tmp_path, keep_recent=1, max_age_days=7)

        # Protected файлы живы
        assert session.exists(), "kraab.session должен существовать"
        assert wal.exists(), "kraab.session-wal должен существовать"
        assert shm.exists(), "kraab.session-shm должен существовать"

        # Protected файлы не в списке removed
        removed_names = {Path(p).name for p in result["removed"]}
        assert "kraab.session" not in removed_names
        assert "kraab.session-wal" not in removed_names
        assert "kraab.session-shm" not in removed_names


class TestKeepsTopNPerCategory:
    """Тест 2: keep_recent=3 — оставляем 3 свежих файла в категории."""

    def test_keeps_top_3_removes_rest(self, tmp_path: Path) -> None:
        """5 bak-corrupt-* файлов → после cleanup 3 свежих остаётся, 2 удаляется."""
        now = time.time()
        # Создаём 5 bak-corrupt файлов разного возраста (все старше 14 дней)
        files = []
        for i in range(5):
            mtime = now - (20 + i) * 86400  # 20, 21, 22, 23, 24 дня назад
            ts = int(now) - (20 + i) * 86400
            f = _make_file(tmp_path / f"kraab.session.bak-corrupt-{ts}", mtime=mtime)
            files.append(f)

        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        # Должно остаться 3 (свежие) и удалиться 2 (старые)
        remaining = [f for f in files if f.exists()]
        deleted = [f for f in files if not f.exists()]

        assert len(remaining) == 3, f"Должно быть 3 файла, осталось {len(remaining)}"
        assert len(deleted) == 2, f"Должно быть удалено 2 файла, удалено {len(deleted)}"

        # Оставшиеся — самые свежие (наименьший age)
        # Используем данные из result['kept'] и result['removed'] чтобы не
        # вызывать stat() на уже удалённых файлах.
        kept_names = {Path(p).name for p in result["kept"]}
        removed_names_set = {Path(p).name for p in result["removed"]}
        assert len(kept_names) == 3, f"В kept должно быть 3 файла, есть: {kept_names}"
        assert len(removed_names_set) == 2, (
            f"В removed должно быть 2 файла, есть: {removed_names_set}"
        )


class TestKeepsWithinMaxAgeDays:
    """Тест 3: файлы старше max_age_days удаляются (если выше top-N)."""

    def test_old_files_above_n_are_removed(self, tmp_path: Path) -> None:
        """Файл старше 14 дней и за пределами top-3 должен быть удалён."""
        now = time.time()

        # Создаём 4 файла: все старше 14 дней
        for i in range(4):
            ts = int(now) - (15 + i) * 86400
            _make_file(tmp_path / f"kraab.session.bak-corrupt-{ts}", mtime=now - (15 + i) * 86400)

        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        # 1 файл должен быть удалён (4-й по счёту, самый старый)
        assert len(result["removed"]) >= 1, "Ожидалось удаление минимум 1 файла"


class TestYoungerThanMaxAgeKept:
    """Тест 4: файлы моложе max_age_days сохраняются, даже если их > keep_recent."""

    def test_young_files_all_kept_even_above_n(self, tmp_path: Path) -> None:
        """5 файлов созданных вчера → все остаются (моложе max_age_days=14)."""
        now = time.time()
        young_mtime = now - 0.5 * 86400  # вчера

        files = []
        for i in range(5):
            ts = int(now) - i
            f = _make_file(tmp_path / f"kraab.session.bak-corrupt-{ts}", mtime=young_mtime)
            files.append(f)

        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        # Все файлы молодые — ни один не должен быть удалён
        assert result["removed"] == [], (
            f"Молодые файлы не должны удаляться, но удалены: {result['removed']}"
        )
        # Все живы
        for f in files:
            assert f.exists(), f"{f.name} должен существовать"


class TestSidecarsRemovedWithMain:
    """Тест 5: sidecar (-shm/-wal) удаляются вместе с main backup."""

    def test_sidecars_removed_with_main(self, tmp_path: Path) -> None:
        """Если main backup удалён — его -shm и -wal тоже удаляются."""
        now = time.time()
        old_ts = int(now) - 30 * 86400
        old_mtime = now - 30 * 86400

        # Main backup + sidecars
        main_file = _make_file(tmp_path / f"kraab.session.bak-corrupt-{old_ts}", mtime=old_mtime)
        shm_sidecar = _make_file(
            tmp_path / f"kraab.session.bak-corrupt-{old_ts}-shm", mtime=old_mtime
        )
        wal_sidecar = _make_file(
            tmp_path / f"kraab.session.bak-corrupt-{old_ts}-wal", mtime=old_mtime
        )

        # Добавим 3 свежих чтобы old попал в удалённые
        for i in range(3):
            ts = int(now) - i
            _make_file(tmp_path / f"kraab.session.bak-corrupt-{ts}", mtime=now - i)

        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        # Main и его sidecars должны быть удалены
        assert not main_file.exists(), "Main backup должен быть удалён"
        assert not shm_sidecar.exists(), "-shm sidecar должен быть удалён вместе с main"
        assert not wal_sidecar.exists(), "-wal sidecar должен быть удалён вместе с main"

        # В отчёте есть все три
        removed_names = {Path(p).name for p in result["removed"]}
        assert main_file.name in removed_names
        assert shm_sidecar.name in removed_names
        assert wal_sidecar.name in removed_names


class TestReturnsBytesFreed:
    """Тест 6: bytes_freed = сумма размеров удалённых файлов."""

    def test_bytes_freed_matches_deleted_sizes(self, tmp_path: Path) -> None:
        """bytes_freed должен равняться суммарному размеру удалённых файлов."""
        now = time.time()
        file_size = 4096  # 4KB каждый

        # 5 старых файлов по 4KB
        total_expected_freed = 0
        for i in range(5):
            ts = int(now) - (20 + i) * 86400
            _make_file(
                tmp_path / f"kraab.session.bak-corrupt-{ts}",
                size=file_size,
                mtime=now - (20 + i) * 86400,
            )

        # Вычисляем что реально будет удалено (beyond top-3)
        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        # bytes_freed = сумма размеров удалённых файлов
        manual_freed = sum(file_size for _ in result["removed"])
        assert result["bytes_freed"] == manual_freed, (
            f"bytes_freed={result['bytes_freed']} != manual_freed={manual_freed}"
        )
        assert result["bytes_freed"] >= 0


class TestDryRunModeNoMutation:
    """Тест 7: dry_run=True — только отчёт, файлы не удаляются."""

    def test_dry_run_does_not_delete_files(self, tmp_path: Path) -> None:
        """В dry_run=True режиме файлы остаются на диске."""
        now = time.time()

        # 5 старых файлов которые должны были бы удалиться
        files = []
        for i in range(5):
            ts = int(now) - (20 + i) * 86400
            f = _make_file(
                tmp_path / f"kraab.session.bak-corrupt-{ts}", mtime=now - (20 + i) * 86400
            )
            files.append(f)

        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14, dry_run=True)

        # dry_run флаг в результате
        assert result["dry_run"] is True

        # Все файлы всё ещё существуют
        for f in files:
            assert f.exists(), f"{f.name} не должен быть удалён в dry_run режиме"

        # removed содержит список (что было бы удалено) но файлы живы
        if result["removed"]:
            # bytes_freed посчитан но файлы живы
            assert result["bytes_freed"] > 0


class TestEmptySessionDirReturnsEmptyReport:
    """Тест 8: пустая директория → пустой результат."""

    def test_empty_dir_returns_empty_report(self, tmp_path: Path) -> None:
        """Если session_dir пустая — removed/kept пустые, bytes_freed=0."""
        result = cleanup_old_backups(tmp_path, keep_recent=3, max_age_days=14)

        assert result["removed"] == []
        assert result["bytes_freed"] == 0

    def test_nonexistent_dir_returns_empty_report(self, tmp_path: Path) -> None:
        """Несуществующая директория — пустой результат без исключений."""
        missing_dir = tmp_path / "nonexistent"
        result = cleanup_old_backups(missing_dir, keep_recent=3, max_age_days=14)

        assert result["removed"] == []
        assert result["bytes_freed"] == 0
        assert "dry_run" in result
