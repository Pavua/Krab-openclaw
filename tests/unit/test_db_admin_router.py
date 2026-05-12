# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.db_admin_router`` — Wave 176 (Session 48).

Покрытие сосредоточено на factory-pattern + sqlite-вызовах. Используем
tmp_path для создания временной БД, чтобы тесты не зависели от реальной
~/.openclaw/.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers import db_admin_router as dar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.db_admin_router import build_db_admin_router

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_client(
    *,
    write_access_raises: Exception | None = None,
) -> TestClient:
    """Создаёт TestClient с подмененным write_access."""

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps={},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_db_admin_router(ctx))
    return TestClient(app)


@pytest.fixture
def fake_openclaw_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Создаёт fake ~/.openclaw структуру с парой реальных БД."""
    # Создаём структуру:
    #   tmp_path/openclaw.db                       — root-level db
    #   tmp_path/krab_memory/archive.db            — main
    #   tmp_path/krab_runtime_state/audit.db       — secondary
    #   tmp_path/chrome-debug-profile/foo.db       — НЕ должна попасть
    krab_memory = tmp_path / "krab_memory"
    krab_memory.mkdir()
    runtime_state = tmp_path / "krab_runtime_state"
    runtime_state.mkdir()
    chrome_dir = tmp_path / "chrome-debug-profile"
    chrome_dir.mkdir()

    # archive.db (main) — добавим табличку с данными.
    archive_path = krab_memory / "archive.db"
    conn = sqlite3.connect(str(archive_path))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO messages (text) VALUES ('hello'), ('world')")
    conn.execute("CREATE TABLE chunks (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    # WAL-файл (имитируем).
    (krab_memory / "archive.db-wal").write_bytes(b"x" * 1024)

    # audit.db — другая БД.
    audit_path = runtime_state / "audit.db"
    conn = sqlite3.connect(str(audit_path))
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()

    # openclaw.db в корне.
    root_db = tmp_path / "openclaw.db"
    conn = sqlite3.connect(str(root_db))
    conn.execute("CREATE TABLE meta (k TEXT)")
    conn.commit()
    conn.close()

    # chrome — должен фильтроваться.
    chrome_db = chrome_dir / "blocked.db"
    conn = sqlite3.connect(str(chrome_db))
    conn.commit()
    conn.close()

    monkeypatch.setattr(dar, "_OPENCLAW_ROOT", tmp_path)
    # Сброс кэша между тестами.
    dar._INTEGRITY_CACHE.clear()

    return tmp_path


# ---------------------------------------------------------------------------
# _humanize_bytes
# ---------------------------------------------------------------------------


def test_humanize_bytes_none() -> None:
    assert dar._humanize_bytes(None) == "—"


def test_humanize_bytes_small() -> None:
    assert dar._humanize_bytes(500) == "500 B"


def test_humanize_bytes_kb() -> None:
    assert "KB" in dar._humanize_bytes(2048)


def test_humanize_bytes_mb() -> None:
    assert "MB" in dar._humanize_bytes(5 * 1024 * 1024)


def test_humanize_bytes_gb() -> None:
    assert "GB" in dar._humanize_bytes(2 * 1024 * 1024 * 1024)


# ---------------------------------------------------------------------------
# _collect_db_files / enumeration
# ---------------------------------------------------------------------------


def test_collect_db_files_finds_known_paths(fake_openclaw_root: Path) -> None:
    paths = dar._collect_db_files()
    rel_names = {p.relative_to(fake_openclaw_root).as_posix() for p in paths}
    assert "krab_memory/archive.db" in rel_names
    assert "krab_runtime_state/audit.db" in rel_names
    assert "openclaw.db" in rel_names


def test_collect_db_files_skips_chrome_dir(fake_openclaw_root: Path) -> None:
    paths = dar._collect_db_files()
    rel_names = {p.relative_to(fake_openclaw_root).as_posix() for p in paths}
    # chrome-debug-profile НЕ в whitelist subdirs.
    assert not any("chrome-debug-profile" in n for n in rel_names)


def test_collect_db_files_handles_missing_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dar, "_OPENCLAW_ROOT", tmp_path / "does_not_exist")
    assert dar._collect_db_files() == []


def test_enumerate_dbs_returns_metadata(fake_openclaw_root: Path) -> None:
    dbs = dar._enumerate_dbs()
    assert any(db["is_main"] for db in dbs)
    main = next(db for db in dbs if db["is_main"])
    assert main["name"] == "krab_memory/archive.db"
    assert main["size"] > 0
    assert main["wal_size"] is not None
    assert main["integrity_status"] == "unknown"  # cache miss


def test_enumerate_dbs_uses_cache_when_recent(fake_openclaw_root: Path) -> None:
    # Подмешаем cache entry.
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    dar._INTEGRITY_CACHE[str(archive_path)] = {
        "ts": time.time(),
        "ok": True,
        "result": "ok",
    }
    dbs = dar._enumerate_dbs()
    main = next(db for db in dbs if db["is_main"])
    assert main["integrity_status"] == "ok"


def test_enumerate_dbs_refresh_ignores_cache(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    dar._INTEGRITY_CACHE[str(archive_path)] = {
        "ts": time.time(),
        "ok": True,
        "result": "ok",
    }
    dbs = dar._enumerate_dbs(refresh=True)
    main = next(db for db in dbs if db["is_main"])
    assert main["integrity_status"] == "unknown"


# ---------------------------------------------------------------------------
# _resolve_db_path
# ---------------------------------------------------------------------------


def test_resolve_db_path_valid(fake_openclaw_root: Path) -> None:
    path = dar._resolve_db_path("krab_memory/archive.db")
    assert path == (fake_openclaw_root / "krab_memory" / "archive.db").resolve()


def test_resolve_db_path_traversal_blocked(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        dar._resolve_db_path("../../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_resolve_db_path_bad_chars_rejected(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        dar._resolve_db_path("foo;rm -rf /")
    assert exc_info.value.status_code == 400


def test_resolve_db_path_not_found(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        dar._resolve_db_path("krab_memory/missing.db")
    assert exc_info.value.status_code == 404


def test_resolve_db_path_subdir_not_in_whitelist(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    # chrome-debug-profile НЕ в _SCAN_SUBDIRS → 403.
    with pytest.raises(HTTPException) as exc_info:
        dar._resolve_db_path("chrome-debug-profile/blocked.db")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# SQL-helpers (sync)
# ---------------------------------------------------------------------------


def test_quick_integrity_check_ok(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    result = dar._quick_integrity_check_sync(archive_path)
    assert result["ok"] is True
    assert "ok" in result["result"].lower()


def test_full_integrity_check_ok(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    result = dar._full_integrity_check_sync(archive_path)
    assert result["ok"] is True


def test_quick_integrity_check_handles_missing_file(tmp_path: Path) -> None:
    result = dar._quick_integrity_check_sync(tmp_path / "missing.db")
    assert result["ok"] is False
    assert "connection_failed" in result["result"]


def test_table_counts_returns_rows(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    result = dar._table_counts_sync(archive_path)
    assert result["ok"] is True
    by_name = {t["name"]: t["row_count"] for t in result["tables"]}
    assert by_name["messages"] == 2
    assert by_name["chunks"] == 0


def test_wal_checkpoint_returns_status(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    # Активируем WAL mode чтобы pragma имела смысл.
    conn = sqlite3.connect(str(archive_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO messages (text) VALUES ('x')")
    conn.commit()
    conn.close()
    result = dar._wal_checkpoint_sync(archive_path)
    # busy=0 + checkpoint выполнен.
    assert result["ok"] is True


def test_vacuum_returns_size_metrics(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    result = dar._vacuum_sync(archive_path)
    assert result["ok"] is True
    assert result["size_before"] is not None
    assert result["size_after"] is not None


# ---------------------------------------------------------------------------
# GET /api/admin/db/list
# ---------------------------------------------------------------------------


def test_db_list_endpoint(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/db/list")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["count"] >= 3
    names = {db["name"] for db in body["dbs"]}
    assert "krab_memory/archive.db" in names
    assert "openclaw.db" in names
    # Main db marker.
    main_entries = [db for db in body["dbs"] if db["is_main"]]
    assert len(main_entries) == 1
    assert main_entries[0]["name"] == "krab_memory/archive.db"


def test_db_list_refresh_param(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    dar._INTEGRITY_CACHE[str(archive_path)] = {
        "ts": time.time(),
        "ok": True,
        "result": "ok",
    }
    client = _make_client()
    resp = client.get("/api/admin/db/list")
    assert any(db["integrity_status"] == "ok" for db in resp.json()["dbs"])
    resp2 = client.get("/api/admin/db/list?refresh=1")
    main = next(db for db in resp2.json()["dbs"] if db["is_main"])
    assert main["integrity_status"] == "unknown"


def test_db_list_handles_internal_error(fake_openclaw_root: Path) -> None:
    with patch.object(dar, "_enumerate_dbs", side_effect=RuntimeError("boom")):
        client = _make_client()
        resp = client.get("/api/admin/db/list")
    assert resp.status_code == 500
    assert "db_list_failed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/admin/db/{db}/tables
# ---------------------------------------------------------------------------


def test_db_tables_endpoint(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/db/krab_memory/archive.db/tables")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    tables = {t["name"]: t["row_count"] for t in body["tables"]}
    assert tables["messages"] == 2


def test_db_tables_invalid_name_returns_400(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/db/bad;name/tables")
    assert resp.status_code == 400


def test_db_tables_not_found(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/api/admin/db/krab_memory/missing.db/tables")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/admin/db/{db}/integrity
# ---------------------------------------------------------------------------


def test_db_integrity_endpoint(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/db/krab_memory/archive.db/integrity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["full"] is False


def test_db_integrity_full_param(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/db/krab_memory/archive.db/integrity?full=1")
    assert resp.status_code == 200
    assert resp.json()["full"] is True


def test_db_integrity_blocked_when_write_access_denied(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/db/krab_memory/archive.db/integrity")
    assert resp.status_code == 403


def test_db_integrity_caches_result(fake_openclaw_root: Path) -> None:
    archive_path = fake_openclaw_root / "krab_memory" / "archive.db"
    client = _make_client()
    client.post("/api/admin/db/krab_memory/archive.db/integrity")
    assert str(archive_path) in dar._INTEGRITY_CACHE


# ---------------------------------------------------------------------------
# POST /api/admin/db/{db}/checkpoint
# ---------------------------------------------------------------------------


def test_db_checkpoint_endpoint(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/db/krab_memory/archive.db/checkpoint")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


def test_db_checkpoint_blocked_when_write_access_denied(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/db/krab_memory/archive.db/checkpoint")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/admin/db/{db}/vacuum
# ---------------------------------------------------------------------------


def test_db_vacuum_requires_confirm(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/db/krab_memory/archive.db/vacuum")
    assert resp.status_code == 400
    assert "confirm" in resp.json()["detail"]


def test_db_vacuum_works_with_confirm(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.post("/api/admin/db/krab_memory/archive.db/vacuum?confirm=yes")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "size_before" in body


def test_db_vacuum_blocked_when_write_access_denied(fake_openclaw_root: Path) -> None:
    from fastapi import HTTPException

    client = _make_client(write_access_raises=HTTPException(status_code=403, detail="forbidden"))
    resp = client.post("/api/admin/db/krab_memory/archive.db/vacuum?confirm=yes")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /admin/db (HTML)
# ---------------------------------------------------------------------------


def test_admin_db_page_returns_html(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/admin/db")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Krab · DB Admin" in resp.text


def test_admin_db_page_has_polling_script(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/admin/db")
    assert "fetchDbs" in resp.text
    assert "setInterval" in resp.text


def test_admin_db_page_renders_actions_buttons(fake_openclaw_root: Path) -> None:
    client = _make_client()
    resp = client.get("/admin/db")
    # Все 4 действия должны иметь триггер.
    assert "runIntegrity" in resp.text
    assert "runCheckpoint" in resp.text
    assert "runVacuum" in resp.text
