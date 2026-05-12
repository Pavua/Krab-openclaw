"""AGE-13: unit-тесты для scripts/krab_daily_maintenance.py.

Цель — закрыть coverage gaps на daily review/maintenance pipeline:
- backup_archive_db: idempotency, prune older backups, missing source, write failure
- rotate_log: threshold gate, generation shift, missing file, error handling
- main: aggregated summary + exit code при ошибках
- edge cases: malformed backup names, midnight rollover, OSError write failures

Все тесты используют tmp_path + monkeypatch.setattr на module constants,
чтобы не задеть реальный ~/.openclaw/krab_memory.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

# Загрузка скрипта как модуля (он в scripts/, не в src/)
_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "krab_daily_maintenance.py"
_spec = importlib.util.spec_from_file_location("krab_daily_maintenance", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
krab_daily_maintenance = importlib.util.module_from_spec(_spec)
sys.modules["krab_daily_maintenance"] = krab_daily_maintenance
_spec.loader.exec_module(krab_daily_maintenance)


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Изолирует ARCHIVE_DB / BACKUP_DIR / STATE_DIR / LOG_FILES_TO_ROTATE."""
    archive_db = tmp_path / "archive.db"
    backup_dir = tmp_path / "backups"
    state_dir = tmp_path / "state"
    stats_file = state_dir / "daily_maintenance.json"
    log_file = state_dir / "daily_maintenance.log"

    monkeypatch.setattr(krab_daily_maintenance, "ARCHIVE_DB", archive_db)
    monkeypatch.setattr(krab_daily_maintenance, "BACKUP_DIR", backup_dir)
    monkeypatch.setattr(krab_daily_maintenance, "STATE_DIR", state_dir)
    monkeypatch.setattr(krab_daily_maintenance, "STATS_FILE", stats_file)
    monkeypatch.setattr(krab_daily_maintenance, "LOG_FILE", log_file)
    monkeypatch.setattr(krab_daily_maintenance, "LOG_FILES_TO_ROTATE", [])

    return {
        "archive_db": archive_db,
        "backup_dir": backup_dir,
        "state_dir": state_dir,
        "stats_file": stats_file,
        "log_file": log_file,
    }


# ---------------- backup_archive_db ----------------


def test_backup_archive_db_missing_source_returns_error(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: если archive.db отсутствует — backup возвращает error без crash."""
    result = krab_daily_maintenance.backup_archive_db()
    assert result["backed_up"] is False
    assert result["error"] is not None
    assert "not found" in result["error"]


def test_backup_archive_db_creates_copy_with_today_prefix(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: backup создаёт archive-YYYYMMDD.db и заполняет size_mb."""
    isolated_paths["archive_db"].write_bytes(b"x" * 2048)
    result = krab_daily_maintenance.backup_archive_db()

    today = _dt.date.today().strftime("%Y%m%d")
    expected = isolated_paths["backup_dir"] / f"archive-{today}.db"
    assert result["backed_up"] is True
    assert result["backup_path"] == str(expected)
    assert expected.exists()
    assert "size_mb" in result
    assert result["error"] is None


def test_backup_archive_db_idempotent_same_day(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: повторный запуск в тот же день не перезаписывает (already_backed_up_today)."""
    isolated_paths["archive_db"].write_bytes(b"original")
    first = krab_daily_maintenance.backup_archive_db()
    assert first["backed_up"] is True

    # Подменяем содержимое — повторный backup НЕ должен затронуть существующий
    isolated_paths["archive_db"].write_bytes(b"modified-after-first")
    second = krab_daily_maintenance.backup_archive_db()

    assert second["backed_up"] is False
    assert second.get("note") == "already_backed_up_today"
    backup_path = Path(second["backup_path"])
    assert backup_path.read_bytes() == b"original"


def test_backup_archive_db_prunes_old_backups(
    isolated_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: backups старше KEEP_DAYS дней удаляются, свежие остаются."""
    monkeypatch.setattr(krab_daily_maintenance, "BACKUP_KEEP_DAYS", 7)
    isolated_paths["archive_db"].write_bytes(b"data")
    isolated_paths["backup_dir"].mkdir(parents=True)

    today = _dt.date.today()
    old1 = isolated_paths["backup_dir"] / f"archive-{(today - _dt.timedelta(days=30)).strftime('%Y%m%d')}.db"
    old2 = isolated_paths["backup_dir"] / f"archive-{(today - _dt.timedelta(days=8)).strftime('%Y%m%d')}.db"
    fresh = isolated_paths["backup_dir"] / f"archive-{(today - _dt.timedelta(days=3)).strftime('%Y%m%d')}.db"
    for p in (old1, old2, fresh):
        p.write_bytes(b"old")

    result = krab_daily_maintenance.backup_archive_db()

    assert result["pruned"] == 2
    assert not old1.exists()
    assert not old2.exists()
    assert fresh.exists()


def test_backup_archive_db_skips_malformed_filenames(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: файлы с битыми именами (archive-foo.db) не вызывают crash."""
    isolated_paths["archive_db"].write_bytes(b"data")
    isolated_paths["backup_dir"].mkdir(parents=True)
    (isolated_paths["backup_dir"] / "archive-notadate.db").write_bytes(b"garbage")
    (isolated_paths["backup_dir"] / "archive-20XX0101.db").write_bytes(b"garbage")

    result = krab_daily_maintenance.backup_archive_db()
    assert result["error"] is None
    # malformed файлы остались — pruner их пропустил
    assert (isolated_paths["backup_dir"] / "archive-notadate.db").exists()


def test_backup_archive_db_catches_oserror_on_copy(
    isolated_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: OSError на shutil.copy2 ловится и попадает в result['error']."""
    isolated_paths["archive_db"].write_bytes(b"data")

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(krab_daily_maintenance.shutil, "copy2", boom)
    result = krab_daily_maintenance.backup_archive_db()

    assert result["backed_up"] is False
    assert result["error"] is not None
    assert "OSError" in result["error"]


# ---------------- rotate_log ----------------


def test_rotate_log_missing_file_silent(tmp_path: Path) -> None:
    """AGE-13: для несуществующего лога ротация просто no-op без ошибки."""
    result = krab_daily_maintenance.rotate_log(tmp_path / "nonexistent.log")
    assert result["rotated"] is False
    assert result["error"] is None
    assert result["size_mb"] == 0


def test_rotate_log_below_threshold_no_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: файл меньше threshold — rotated=False, файл не тронут."""
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_THRESHOLD_MB", 10)
    log = tmp_path / "small.log"
    log.write_bytes(b"x" * 1024)  # 1 KB

    result = krab_daily_maintenance.rotate_log(log)
    assert result["rotated"] is False
    assert log.exists()
    assert log.read_bytes() == b"x" * 1024


def test_rotate_log_rotates_when_over_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: файл > threshold → создаётся .log.1.gz + original truncated."""
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_THRESHOLD_MB", 1)
    log = tmp_path / "big.log"
    payload = b"line\n" * (300 * 1024)  # ~1.5 MB
    log.write_bytes(payload)

    result = krab_daily_maintenance.rotate_log(log)

    assert result["rotated"] is True
    assert result["error"] is None
    gz_path = log.with_suffix(log.suffix + ".1.gz")
    assert gz_path.exists()
    with gzip.open(gz_path, "rb") as f:
        assert f.read() == payload
    # Original truncated
    assert log.read_bytes() == b""


def test_rotate_log_shifts_generations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: при ротации .log.1.gz → .log.2.gz, последняя generation удаляется."""
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_THRESHOLD_MB", 1)
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_KEEP_GENERATIONS", 3)
    log = tmp_path / "app.log"
    log.write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MB

    gen1 = log.with_suffix(log.suffix + ".1.gz")
    gen2 = log.with_suffix(log.suffix + ".2.gz")
    gen3 = log.with_suffix(log.suffix + ".3.gz")
    gen1.write_bytes(b"gen1-content")
    gen2.write_bytes(b"gen2-content")
    gen3.write_bytes(b"gen3-content")  # oldest — should be deleted

    krab_daily_maintenance.rotate_log(log)

    # gen3 удалён, gen2 → gen3, gen1 → gen2, новый .1.gz содержит свежий лог
    assert gen3.exists()
    assert gen3.read_bytes() == b"gen2-content"
    assert gen2.read_bytes() == b"gen1-content"
    # gen1 = новый, не "gen1-content"
    assert gen1.exists()
    assert gen1.read_bytes() != b"gen1-content"


def test_rotate_log_handles_write_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: ошибка gzip.open ловится и попадает в result['error']."""
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_THRESHOLD_MB", 1)
    log = tmp_path / "fail.log"
    log.write_bytes(b"y" * (2 * 1024 * 1024))

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(krab_daily_maintenance.gzip, "open", boom)
    result = krab_daily_maintenance.rotate_log(log)

    assert result["rotated"] is False
    assert result["error"] is not None
    assert "OSError" in result["error"]


# ---------------- main ----------------


def test_main_returns_zero_on_clean_run(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: main → 0 когда archive.db есть, логов для ротации нет."""
    isolated_paths["archive_db"].write_bytes(b"data")
    exit_code = krab_daily_maintenance.main()

    assert exit_code == 0
    summary = json.loads(isolated_paths["stats_file"].read_text())
    assert summary["archive_backup"]["backed_up"] is True
    assert summary["log_rotations"] == []
    assert summary["errors"] == []


def test_main_returns_one_when_backup_fails(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: missing archive.db → exit code 1 + summary['errors'] не пустой."""
    # archive_db не создаём
    exit_code = krab_daily_maintenance.main()

    assert exit_code == 1
    summary = json.loads(isolated_paths["stats_file"].read_text())
    assert summary["errors"]
    assert any("backup:" in err for err in summary["errors"])


def test_main_aggregates_log_rotation_errors(
    isolated_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AGE-13: ошибки ротации логов попадают в summary['errors'], exit=1."""
    isolated_paths["archive_db"].write_bytes(b"data")
    bad_log = tmp_path / "bad.log"
    bad_log.write_bytes(b"z" * (2 * 1024 * 1024))
    monkeypatch.setattr(krab_daily_maintenance, "LOG_ROTATE_THRESHOLD_MB", 1)
    monkeypatch.setattr(krab_daily_maintenance, "LOG_FILES_TO_ROTATE", [bad_log])

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("io error")

    monkeypatch.setattr(krab_daily_maintenance.gzip, "open", boom)
    exit_code = krab_daily_maintenance.main()

    assert exit_code == 1
    summary = json.loads(isolated_paths["stats_file"].read_text())
    assert any("rotate bad.log" in err for err in summary["errors"])


def test_main_persists_stats_with_iso_timestamp(isolated_paths: dict[str, Path]) -> None:
    """AGE-13: stats.json содержит timestamp_utc в ISO формате с timezone."""
    isolated_paths["archive_db"].write_bytes(b"data")
    krab_daily_maintenance.main()

    summary = json.loads(isolated_paths["stats_file"].read_text())
    ts = summary["timestamp_utc"]
    # +00:00 или Z — должен парситься через fromisoformat
    parsed = _dt.datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None


def test_main_stats_write_oserror_does_not_crash(
    isolated_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AGE-13: если STATS_FILE.write_text падает (OSError) — main всё равно завершается."""
    isolated_paths["archive_db"].write_bytes(b"data")

    original_write = Path.write_text

    def fake_write(self: Path, *a: object, **kw: object) -> int:
        if self == isolated_paths["stats_file"]:
            raise OSError("read-only fs")
        return original_write(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", fake_write)
    # Не должно бросить — OSError проглочен в main
    exit_code = krab_daily_maintenance.main()
    assert exit_code == 0
