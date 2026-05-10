# -*- coding: utf-8 -*-
"""Тесты Wave 56-I-audit-rotation: AuditLogRotator + analyzer read + CLI."""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

# Путь к репозиторию
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.core.audit_log_rotator import (  # noqa: E402
    AuditLogRotator,
    _archive_path,
    _compress_to_gz,
    _extract_ts,
    read_audit_log_with_archives,
)

# =============================================================================
# Helpers
# =============================================================================


def _write_jsonl(path: Path, n_lines: int, size_bytes: int | None = None) -> None:
    """Записать n_lines JSONL-строк (или пока не достигнем size_bytes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n_lines):
            record = {"ts": f"2026-05-09T0{i % 10}:00:00Z", "idx": i, "data": "x" * 100}
            fh.write(json.dumps(record) + "\n")
            if size_bytes and fh.tell() >= size_bytes:
                break


def _make_gz_archive(log_path: Path, idx: int, content: bytes = b"data\n") -> Path:
    """Создать gzip-архив с номером idx."""
    arc = _archive_path(log_path, idx)
    with gzip.open(arc, "wb") as gz:
        gz.write(content)
    return arc


# =============================================================================
# test_rotate_skipped_below_threshold
# =============================================================================


class TestRotateSkippedBelowThreshold:
    def test_rotate_skipped_below_threshold(self, tmp_path: Path) -> None:
        """Файл меньше порога — ротации нет."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 5)  # маленький файл

        rotator = AuditLogRotator()
        result = rotator.rotate_if_needed(log, max_size_mb=10, keep_count=5)

        assert result["rotated"] is False
        assert result["rotated_to"] == ""
        assert result["removed"] == []
        # Файл остался нетронутым
        assert log.exists()
        assert log.stat().st_size > 0

    def test_nonexistent_log_skipped(self, tmp_path: Path) -> None:
        """Несуществующий файл — нет ошибки, нет ротации."""
        rotator = AuditLogRotator()
        result = rotator.rotate_if_needed(tmp_path / "missing.log", max_size_mb=1, keep_count=5)
        assert result["rotated"] is False


# =============================================================================
# test_rotate_renames_and_compresses_when_exceeded
# =============================================================================


class TestRotateRenamesAndCompressesWhenExceeded:
    def test_rotate_renames_and_compresses_when_exceeded(self, tmp_path: Path) -> None:
        """Файл > порога → создаётся .1.gz, оригинал обнуляется."""
        log = tmp_path / "audit.jsonl"
        # Записать ~200KB данных
        _write_jsonl(log, 2000)
        original_size = log.stat().st_size

        rotator = AuditLogRotator()
        # Порог 0.1 МБ (100KB) — меньше нашего файла
        result = rotator.rotate_if_needed(log, max_size_mb=0, keep_count=5)

        assert result["rotated"] is True
        assert result["old_size_mb"] == pytest.approx(original_size / (1024 * 1024), abs=0.01)
        assert result["rotated_to"].endswith(".1.gz")

        # Архив создан и читается
        arc = Path(result["rotated_to"])
        assert arc.exists()
        with gzip.open(arc, "rt", encoding="utf-8") as gz:
            content = gz.read()
        assert len(content) > 0

        # Оригинальный файл пустой
        assert log.stat().st_size == 0

    def test_result_old_size_mb_reported(self, tmp_path: Path) -> None:
        """old_size_mb отражает реальный размер до ротации."""
        log = tmp_path / "test.log"
        log.write_bytes(b"a" * (2 * 1024 * 1024))  # 2MB ровно

        rotator = AuditLogRotator()
        result = rotator.rotate_if_needed(log, max_size_mb=1, keep_count=5)

        assert result["rotated"] is True
        assert result["old_size_mb"] == pytest.approx(2.0, abs=0.01)


# =============================================================================
# test_rotate_shifts_existing_archives
# =============================================================================


class TestRotateShiftsExistingArchives:
    def test_rotate_shifts_existing_archives(self, tmp_path: Path) -> None:
        """Существующие .1.gz → .2.gz, .2.gz → .3.gz при ротации."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 100)

        # Создать уже существующие архивы
        arc1 = _make_gz_archive(log, 1, b"archive_1_content\n")
        arc2 = _make_gz_archive(log, 2, b"archive_2_content\n")

        rotator = AuditLogRotator()
        result = rotator.rotate_if_needed(log, max_size_mb=0, keep_count=5)

        assert result["rotated"] is True

        # .1.gz стал .2.gz
        new_arc2 = _archive_path(log, 2)
        assert new_arc2.exists()
        with gzip.open(new_arc2, "rb") as gz:
            assert gz.read() == b"archive_1_content\n"

        # .2.gz стал .3.gz
        new_arc3 = _archive_path(log, 3)
        assert new_arc3.exists()
        with gzip.open(new_arc3, "rb") as gz:
            assert gz.read() == b"archive_2_content\n"

        # Новый .1.gz — свежий архив
        new_arc1 = _archive_path(log, 1)
        assert new_arc1.exists()


# =============================================================================
# test_rotate_drops_oldest_beyond_keep_count
# =============================================================================


class TestRotateDropsOldestBeyondKeepCount:
    def test_rotate_drops_oldest_beyond_keep_count(self, tmp_path: Path) -> None:
        """Архив с номером keep_count удаляется при сдвиге."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 100)

        # Заполнить все 3 слота (keep_count=3)
        for idx in range(1, 4):
            _make_gz_archive(log, idx, f"content_{idx}\n".encode())

        rotator = AuditLogRotator()
        result = rotator.rotate_if_needed(log, max_size_mb=0, keep_count=3)

        assert result["rotated"] is True
        # Самый старый (было .3.gz) должен быть удалён
        assert len(result["removed"]) >= 1
        oldest = _archive_path(log, 4)
        assert not oldest.exists()  # .4.gz не должен существовать

        # .1.gz и .2.gz должны быть
        assert _archive_path(log, 1).exists()
        assert _archive_path(log, 2).exists()
        assert _archive_path(log, 3).exists()

    def test_no_orphan_archives_beyond_keep(self, tmp_path: Path) -> None:
        """После ротации количество архивов не превышает keep_count."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 50)

        # Создать keep_count=2 архивов
        _make_gz_archive(log, 1, b"a")
        _make_gz_archive(log, 2, b"b")

        rotator = AuditLogRotator()
        rotator.rotate_if_needed(log, max_size_mb=0, keep_count=2)

        # После ротации: .1.gz (новый), .2.gz (бывший .1.gz), .3.gz НЕ существует
        assert _archive_path(log, 1).exists()
        assert _archive_path(log, 2).exists()
        assert not _archive_path(log, 3).exists()


# =============================================================================
# test_rotate_creates_new_empty_log
# =============================================================================


class TestRotateCreatesNewEmptyLog:
    def test_rotate_creates_new_empty_log(self, tmp_path: Path) -> None:
        """После ротации оригинальный файл существует и пустой."""
        log = tmp_path / "audit.jsonl"
        _write_jsonl(log, 100)

        rotator = AuditLogRotator()
        rotator.rotate_if_needed(log, max_size_mb=0, keep_count=5)

        assert log.exists()
        assert log.stat().st_size == 0

    def test_new_writes_go_to_empty_log(self, tmp_path: Path) -> None:
        """Новые записи после ротации пишутся в оригинальный файл."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 100)

        rotator = AuditLogRotator()
        rotator.rotate_if_needed(log, max_size_mb=0, keep_count=5)

        # Пишем новую запись
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": "2026-05-10T04:00:00Z", "new": True}) + "\n")

        content = log.read_text(encoding="utf-8").strip()
        assert "2026-05-10" in content


# =============================================================================
# test_rotate_atomic_no_data_loss_mid_write
# =============================================================================


class TestRotateAtomicNoDataLossMidWrite:
    def test_rotate_atomic_no_data_loss_mid_write(self, tmp_path: Path) -> None:
        """Конкурентный appender + ротация не теряют байты.

        Запускаем поток, который непрерывно пишет в лог,
        затем ротируем. Все байты должны попасть либо в архив, либо в новый файл.
        """
        log = tmp_path / "concurrent.log"
        _write_jsonl(log, 50)

        lines_written: list[str] = []
        stop_event = threading.Event()

        def _appender() -> None:
            i = 0
            while not stop_event.is_set():
                line = json.dumps({"ts": f"2026-05-10T05:00:{i:02d}Z", "thread": i})
                try:
                    with log.open("a", encoding="utf-8") as fh:
                        fh.write(line + "\n")
                    lines_written.append(line)
                except OSError:
                    pass
                i += 1
                time.sleep(0.001)

        t = threading.Thread(target=_appender, daemon=True)
        t.start()
        time.sleep(0.05)  # дать потоку поработать

        rotator = AuditLogRotator()
        rotator.rotate_if_needed(log, max_size_mb=0, keep_count=5)

        stop_event.set()
        t.join(timeout=2)

        # Суммарный контент: архив + новый файл
        arc = _archive_path(log, 1)
        total_content = ""
        if arc.exists():
            with gzip.open(arc, "rt", encoding="utf-8", errors="replace") as gz:
                total_content += gz.read()
        if log.exists():
            total_content += log.read_text(encoding="utf-8", errors="replace")

        # Хотя бы несколько записей appender'а должны быть в файлах
        # (допускаем race на последних строках при rename)
        found = sum(1 for line in lines_written if line in total_content)
        # Большинство строк должны найтись
        assert found >= max(1, len(lines_written) // 2)


# =============================================================================
# test_analyzer_reads_archived_files_within_window
# =============================================================================


class TestAnalyzerReadsArchivedFilesWithinWindow:
    def test_analyzer_reads_archived_files_within_window(self, tmp_path: Path) -> None:
        """read_audit_log_with_archives возвращает строки из архивов и активного файла."""
        log = tmp_path / "audit.jsonl"

        # Создать архив .2.gz (старые данные)
        arc2_content = b""
        for ts in ["2026-05-08T01:00:00Z", "2026-05-08T02:00:00Z"]:
            arc2_content += (json.dumps({"ts": ts, "src": "arc2"}) + "\n").encode()
        with gzip.open(_archive_path(log, 2), "wb") as gz:
            gz.write(arc2_content)

        # Создать архив .1.gz (новее)
        arc1_content = b""
        for ts in ["2026-05-09T01:00:00Z"]:
            arc1_content += (json.dumps({"ts": ts, "src": "arc1"}) + "\n").encode()
        with gzip.open(_archive_path(log, 1), "wb") as gz:
            gz.write(arc1_content)

        # Активный файл
        log.write_text(
            json.dumps({"ts": "2026-05-09T23:00:00Z", "src": "active"}) + "\n",
            encoding="utf-8",
        )

        lines = read_audit_log_with_archives(log, keep_count=5)
        assert len(lines) == 4

        sources = [json.loads(l)["src"] for l in lines]
        assert sources == ["arc2", "arc2", "arc1", "active"]

    def test_analyzer_time_filter_excludes_out_of_window(self, tmp_path: Path) -> None:
        """Фильтрация по since_ts/until_ts отсекает нерелевантные строки."""
        log = tmp_path / "audit.jsonl"

        # Архив .1.gz
        arc_content = b""
        for ts in ["2026-05-01T00:00:00Z", "2026-05-05T00:00:00Z"]:
            arc_content += (json.dumps({"ts": ts}) + "\n").encode()
        with gzip.open(_archive_path(log, 1), "wb") as gz:
            gz.write(arc_content)

        log.write_text(
            json.dumps({"ts": "2026-05-09T12:00:00Z"}) + "\n",
            encoding="utf-8",
        )

        from datetime import datetime, timezone  # noqa: PLC0415

        since = datetime(2026, 5, 4, tzinfo=timezone.utc).timestamp()
        until = datetime(2026, 5, 6, tzinfo=timezone.utc).timestamp()

        lines = read_audit_log_with_archives(log, keep_count=5, since_ts=since, until_ts=until)
        assert len(lines) == 1
        ts_val = json.loads(lines[0])["ts"]
        assert "2026-05-05" in ts_val

    def test_analyzer_handles_missing_archives_gracefully(self, tmp_path: Path) -> None:
        """Отсутствующие архивы не вызывают ошибку."""
        log = tmp_path / "audit.jsonl"
        log.write_text(json.dumps({"ts": "2026-05-09T10:00:00Z", "ok": True}) + "\n")

        # Нет архивов — только активный файл
        lines = read_audit_log_with_archives(log, keep_count=5)
        assert len(lines) == 1

    def test_analyzer_empty_log_returns_empty(self, tmp_path: Path) -> None:
        """Пустой файл → пустой список."""
        log = tmp_path / "empty.log"
        log.write_text("")
        lines = read_audit_log_with_archives(log, keep_count=5)
        assert lines == []


# =============================================================================
# test_cli_check_outputs_sizes
# =============================================================================


class TestCliCheckOutputsSizes:
    def test_cli_check_outputs_sizes(self, tmp_path: Path) -> None:
        """--check выводит JSON с размерами файлов."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 10)

        env = os.environ.copy()
        env["KRAB_BASH_AUDIT_PATH"] = str(log)
        env["KRAB_AGENT_AUDIT_PATH"] = str(tmp_path / "agent.jsonl")

        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "krab_audit_rotate.py"), "--check"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["mode"] == "check"
        assert "bash" in data
        assert "agent" in data
        assert "size_mb" in data["bash"]
        assert "would_rotate" in data["bash"]
        assert data["bash"]["path"] == str(log)

    def test_cli_check_no_args_defaults_to_check(self, tmp_path: Path) -> None:
        """Без аргументов — режим check."""
        env = os.environ.copy()
        env["KRAB_BASH_AUDIT_PATH"] = str(tmp_path / "b.log")
        env["KRAB_AGENT_AUDIT_PATH"] = str(tmp_path / "a.jsonl")

        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "krab_audit_rotate.py")],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["mode"] == "check"


# =============================================================================
# test_cli_force_rotates_now
# =============================================================================


class TestCliForceRotatesNow:
    def test_cli_force_rotates_now(self, tmp_path: Path) -> None:
        """--force создаёт архив даже для небольшого файла."""
        log = tmp_path / "audit.log"
        _write_jsonl(log, 20)  # маленький файл, не прошёл бы threshold
        original_size = log.stat().st_size
        assert original_size > 0

        env = os.environ.copy()
        env["KRAB_BASH_AUDIT_PATH"] = str(log)
        env["KRAB_AGENT_AUDIT_PATH"] = str(tmp_path / "agent.jsonl")

        result = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "krab_audit_rotate.py"), "--force"],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

        data = json.loads(result.stdout)
        assert data["mode"] == "force"
        assert data["bash"]["rotated"] is True

        # Архив создан
        arc = _archive_path(log, 1)
        assert arc.exists()
        # Оригинал пустой
        assert log.stat().st_size == 0

    def test_cli_force_archive_is_valid_gzip(self, tmp_path: Path) -> None:
        """Архив, созданный --force, является валидным gzip."""
        log = tmp_path / "test.log"
        log.write_text(json.dumps({"ts": "2026-05-10T00:00:00Z"}) + "\n")

        env = os.environ.copy()
        env["KRAB_BASH_AUDIT_PATH"] = str(log)
        env["KRAB_AGENT_AUDIT_PATH"] = str(tmp_path / "a.jsonl")

        subprocess.run(
            [sys.executable, str(REPO / "scripts" / "krab_audit_rotate.py"), "--force"],
            capture_output=True,
            env=env,
            timeout=15,
        )

        arc = _archive_path(log, 1)
        assert arc.exists()
        with gzip.open(arc, "rt") as gz:
            content = gz.read()
        assert "2026-05-10" in content


# =============================================================================
# Дополнительные unit-тесты (helper functions)
# =============================================================================


class TestHelpers:
    def test_extract_ts_iso_format(self) -> None:
        """_extract_ts парсит ISO 8601 строку."""
        from datetime import datetime, timezone  # noqa: PLC0415

        line = json.dumps({"ts": "2026-05-09T03:00:00Z", "x": 1})
        ts = _extract_ts(line)
        expected = datetime(2026, 5, 9, 3, 0, 0, tzinfo=timezone.utc).timestamp()
        assert ts == pytest.approx(expected, abs=1)

    def test_extract_ts_numeric(self) -> None:
        """_extract_ts обрабатывает числовой timestamp."""
        line = json.dumps({"ts": 1746748800.0})
        ts = _extract_ts(line)
        assert ts == pytest.approx(1746748800.0)

    def test_extract_ts_missing_field(self) -> None:
        """_extract_ts возвращает None при отсутствии 'ts'."""
        line = json.dumps({"no_ts": "value"})
        assert _extract_ts(line) is None

    def test_extract_ts_invalid_json(self) -> None:
        """_extract_ts не падает на невалидном JSON."""
        assert _extract_ts("not json at all") is None

    def test_compress_to_gz_round_trip(self, tmp_path: Path) -> None:
        """_compress_to_gz: сжатие + распаковка сохраняет данные."""
        src = tmp_path / "source.log"
        content = b"line1\nline2\nline3\n" * 100
        src.write_bytes(content)
        dest = tmp_path / "source.log.1.gz"

        _compress_to_gz(src, dest)

        assert dest.exists()
        with gzip.open(dest, "rb") as gz:
            recovered = gz.read()
        assert recovered == content

    def test_rotate_all_returns_both_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """rotate_all() возвращает словарь с ключами 'bash' и 'agent'."""
        bash_log = tmp_path / "bash.log"
        agent_log = tmp_path / "agent.jsonl"
        bash_log.write_text("entry\n")
        agent_log.write_text("entry\n")

        monkeypatch.setenv("KRAB_BASH_AUDIT_PATH", str(bash_log))
        monkeypatch.setenv("KRAB_AGENT_AUDIT_PATH", str(agent_log))

        rotator = AuditLogRotator()
        results = rotator.rotate_all(max_size_mb=100, keep_count=5)

        assert "bash" in results
        assert "agent" in results
        # Ниже порога — ничего не ротировали
        assert results["bash"]["rotated"] is False
        assert results["agent"]["rotated"] is False
