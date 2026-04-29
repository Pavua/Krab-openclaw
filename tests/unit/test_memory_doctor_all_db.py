# -*- coding: utf-8 -*-
"""Тесты для расширения memory_doctor.check_all_databases (Session 28).

Покрывают:
1. integrity_check OK на здоровой sqlite.
2. Detection malformed/corrupted db (через испорченный заголовок).
3. Graceful skip отсутствующего файла + 0-байтового stub.
4. WAL/journal mode корректно подхватывается.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# Подгружаем scripts/memory_doctor.py как модуль (он не в src/, не в sys.path).
_DOCTOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "memory_doctor.py"
_spec = importlib.util.spec_from_file_location("krab_memory_doctor", _DOCTOR_PATH)
assert _spec and _spec.loader
memory_doctor = importlib.util.module_from_spec(_spec)
sys.modules["krab_memory_doctor"] = memory_doctor
_spec.loader.exec_module(memory_doctor)


def _make_healthy_db(path: Path, *, wal: bool = False) -> None:
    """Создаёт минимальную работоспособную sqlite-базу."""
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO t (val) VALUES ('hello'), ('world')")
    conn.commit()
    conn.close()


def _make_corrupt_db(path: Path) -> None:
    """Создаёт файл, который sqlite распознает как malformed.

    Подход: создаём базу с достаточным объёмом данных (несколько страниц),
    потом затираем мусором страницу #2+ — header остаётся валидным
    (sqlite откроет файл), но `PRAGMA integrity_check` вернёт btree errors.
    """
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    for _ in range(2000):
        conn.execute("INSERT INTO t (val) VALUES (?)", ("x" * 200,))
    conn.commit()
    conn.close()
    # Затираем страницы 2..N мусором (header первой страницы трогать нельзя —
    # иначе sqlite вернёт "file is not a database" ещё до integrity_check).
    with open(path, "r+b") as fh:
        fh.seek(4096)  # начало второй страницы при page_size=4096
        fh.write(b"\x00\xFF\xCC\xAA" * 4096)


# --- check_single_db ---


def test_check_single_db_healthy(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_healthy_db(db, wal=True)
    entry = memory_doctor.KnownDb(path=db, kind="cache", owner="test")
    rep = memory_doctor.check_single_db(entry)
    assert rep.exists is True
    assert rep.empty is False
    assert rep.ok is True
    assert rep.corrupt is False
    assert rep.integrity.lower() == "ok"
    assert rep.quick_check.lower() == "ok"
    assert rep.journal_mode.lower() == "wal"
    assert rep.size_bytes > 0


def test_check_single_db_missing(tmp_path: Path) -> None:
    entry = memory_doctor.KnownDb(
        path=tmp_path / "nope.db", kind="cache", owner="test"
    )
    rep = memory_doctor.check_single_db(entry)
    assert rep.exists is False
    assert rep.ok is True  # missing == graceful skip
    assert rep.corrupt is False
    assert rep.error == ""


def test_check_single_db_empty_stub(tmp_path: Path) -> None:
    db = tmp_path / "stub.db"
    db.touch()
    entry = memory_doctor.KnownDb(path=db, kind="cache", owner="test")
    rep = memory_doctor.check_single_db(entry)
    assert rep.exists is True
    assert rep.empty is True
    assert rep.ok is True  # 0-байтовый stub допустим
    assert rep.corrupt is False


def test_check_single_db_corrupt(tmp_path: Path) -> None:
    db = tmp_path / "broken.db"
    _make_corrupt_db(db)
    entry = memory_doctor.KnownDb(path=db, kind="archive", owner="test")
    rep = memory_doctor.check_single_db(entry)
    assert rep.exists is True
    assert rep.empty is False
    assert rep.ok is False
    # Либо PRAGMA вернёт !=ok, либо exception про malformed —
    # в обоих случаях corrupt=True
    assert rep.corrupt is True


# --- check_all_databases (sweep) ---


def test_check_all_databases_mixed(tmp_path: Path) -> None:
    healthy = tmp_path / "good.db"
    _make_healthy_db(healthy)
    bad = tmp_path / "bad.db"
    _make_corrupt_db(bad)
    missing = tmp_path / "missing.db"
    stub = tmp_path / "stub.db"
    stub.touch()

    entries = [
        memory_doctor.KnownDb(healthy, "cache", "good"),
        memory_doctor.KnownDb(bad, "archive", "bad"),
        memory_doctor.KnownDb(missing, "cache", "absent"),
        memory_doctor.KnownDb(stub, "cache", "stub"),
    ]
    summary = memory_doctor.check_all_databases(entries)
    assert summary["total"] == 4
    assert summary["corrupt_count"] == 1
    assert summary["missing_count"] == 1
    assert summary["empty_count"] == 1
    assert summary["all_ok"] is False
    paths = {r["path"]: r for r in summary["reports"]}
    assert paths[str(healthy)]["ok"] is True
    assert paths[str(bad)]["corrupt"] is True
    assert paths[str(missing)]["exists"] is False
    assert paths[str(stub)]["empty"] is True


def test_check_all_databases_default_paths_runnable() -> None:
    """known_db_paths() возвращает list и check_all_databases не падает.

    Это smoke-тест: на dev-машине файлы могут отсутствовать или быть
    рабочими — проверяем что обход не валится.
    """
    entries = memory_doctor.known_db_paths()
    assert isinstance(entries, list) and entries
    summary = memory_doctor.check_all_databases(entries)
    assert "reports" in summary
    assert summary["total"] == len(entries)
    # Не утверждаем all_ok=True — на live-машине состояние может быть любым,
    # но падать функция не должна.


def test_is_corruption_text_markers() -> None:
    assert memory_doctor._is_corruption_text(
        "database disk image is malformed"
    )
    assert memory_doctor._is_corruption_text("disk I/O error")
    assert memory_doctor._is_corruption_text("file is not a database")
    assert not memory_doctor._is_corruption_text("ok")
    assert not memory_doctor._is_corruption_text("")


# --- DbReport.to_dict serialization ---


def test_db_report_to_dict_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "ok.db"
    _make_healthy_db(db)
    rep = memory_doctor.check_single_db(
        memory_doctor.KnownDb(db, "cache", "test")
    )
    d = rep.to_dict()
    for key in ("path", "kind", "owner", "exists", "ok", "corrupt", "integrity"):
        assert key in d


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
