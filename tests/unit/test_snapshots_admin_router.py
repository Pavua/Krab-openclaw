# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.snapshots_admin_router`` — Wave 226.

Покрытие: factory pattern + endpoint contracts. Используем ``tmp_path``
для изоляции от реальной ``~/.openclaw/krab_runtime_state/snapshots/``.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.state_snapshots import StateSnapshotManager
from src.modules.web_routers import snapshots_admin_router as sar
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.snapshots_admin_router import (
    build_snapshots_admin_router,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(tmp_path: Path) -> StateSnapshotManager:
    """StateSnapshotManager поверх tmp_path с парой fake snapshots."""
    runtime_dir = tmp_path / "runtime_state"
    runtime_dir.mkdir()

    # Минимальный реальный manager — будет писать в tmp_path/runtime_state/snapshots/
    mgr = StateSnapshotManager(runtime_state_dir=runtime_dir)

    # Создадим source файлы (чтобы manager.snapshot_now было что копировать).
    (runtime_dir / "inbox_state.json").write_text(
        json.dumps({"items": [], "version": 1}), encoding="utf-8"
    )
    (runtime_dir / "last_seen_messages.json").write_text(
        json.dumps({"chat_42": 999}), encoding="utf-8"
    )
    (runtime_dir / "route_switches.jsonl").write_text(
        '{"ts": 1700000000, "from": "a", "to": "b"}\n', encoding="utf-8"
    )

    # Создадим один pre-existing snapshot вручную.
    snap_dir = mgr.snapshot_root / "20260509T120000Z"
    snap_dir.mkdir(parents=True)
    (snap_dir / "inbox_state.json.bak").write_text(
        json.dumps({"items": ["old"], "version": 1}), encoding="utf-8"
    )
    return mgr


def _make_client(
    manager: StateSnapshotManager,
    *,
    write_access_raises: Exception | None = None,
) -> TestClient:
    """TestClient с inject'нутым кастомным manager."""

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps={"state_snapshot_manager": manager},
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_snapshots_admin_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_humanize_bytes_branches() -> None:
    assert sar._humanize_bytes(None) == "—"
    assert sar._humanize_bytes(500) == "500 B"
    assert "KB" in sar._humanize_bytes(2048)
    assert "MB" in sar._humanize_bytes(5 * 1024 * 1024)
    assert "GB" in sar._humanize_bytes(3 * 1024 * 1024 * 1024)


def test_parse_timestamp_to_iso() -> None:
    assert sar._parse_timestamp_to_iso("20260509T225605Z") == "2026-05-09T22:56:05Z"
    assert sar._parse_timestamp_to_iso("_pre_restore_20260509T225605Z") == "2026-05-09T22:56:05Z"
    assert sar._parse_timestamp_to_iso("not_a_timestamp") is None


def test_validate_snapshot_name_rejects_traversal() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        sar._validate_snapshot_name("../../etc/passwd")
    assert exc.value.status_code == 400


def test_validate_snapshot_name_rejects_bad_chars() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        sar._validate_snapshot_name("foo;rm -rf /")
    assert exc.value.status_code == 400


def test_validate_snapshot_name_accepts_valid() -> None:
    assert sar._validate_snapshot_name("20260509T120000Z") == "20260509T120000Z"
    assert (
        sar._validate_snapshot_name("_pre_restore_20260509T120000Z")
        == "_pre_restore_20260509T120000Z"
    )


# ---------------------------------------------------------------------------
# /api/admin/snapshots/list
# ---------------------------------------------------------------------------


def test_list_endpoint_returns_snapshots(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/list")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] >= 1
    assert any(s["timestamp"] == "20260509T120000Z" for s in data["snapshots"])
    assert "groups" in data
    assert "interval_minutes" in data
    assert data["snapshot_root"] == str(manager.snapshot_root)


def test_list_endpoint_enriches_metadata(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    data = client.get("/api/admin/snapshots/list").json()
    row = next(s for s in data["snapshots"] if s["timestamp"] == "20260509T120000Z")
    assert row["size_human"]
    assert row["timestamp_iso"] == "2026-05-09T12:00:00Z"
    assert row["is_pre_restore"] is False
    assert row["age_sec"] is not None


# ---------------------------------------------------------------------------
# /api/admin/snapshots/{name}/preview
# ---------------------------------------------------------------------------


def test_preview_endpoint_default_file(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/20260509T120000Z/preview")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["file"] == "inbox_state.json.bak"
    # pretty генерируется потому что .json.bak.
    assert data["pretty"] is not None
    assert "items" in data["pretty"]


def test_preview_endpoint_explicit_file(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get(
        "/api/admin/snapshots/20260509T120000Z/preview",
        params={"file": "inbox_state.json.bak"},
    )
    assert res.status_code == 200


def test_preview_endpoint_invalid_name(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/..%2F..%2Fetc/preview")
    assert res.status_code in (400, 404)


def test_preview_endpoint_missing_snapshot(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/20260101T000000Z/preview")
    assert res.status_code == 404


def test_preview_endpoint_file_outside_snapshot(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get(
        "/api/admin/snapshots/20260509T120000Z/preview",
        params={"file": "nope_does_not_exist.bak"},
    )
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/admin/snapshots/trigger
# ---------------------------------------------------------------------------


def test_trigger_endpoint_creates_new_snapshot(
    manager: StateSnapshotManager,
) -> None:
    client = _make_client(manager)
    before = len(manager.list_snapshots())
    res = client.post("/api/admin/snapshots/trigger")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["reason"] == "manual"
    assert len(data["copied"]) >= 1
    # Manager должен видеть новый snapshot.
    after = len(manager.list_snapshots())
    assert after == before + 1


def test_trigger_endpoint_enforces_write_access(
    manager: StateSnapshotManager,
) -> None:
    from fastapi import HTTPException

    client = _make_client(
        manager,
        write_access_raises=HTTPException(status_code=403, detail="forbidden"),
    )
    res = client.post("/api/admin/snapshots/trigger")
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# /api/admin/snapshots/{name}/download
# ---------------------------------------------------------------------------


def test_download_endpoint_returns_targz(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/20260509T120000Z/download")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/gzip"
    assert "20260509T120000Z.tar.gz" in res.headers["content-disposition"]

    # Содержимое — валидный tar.gz с одним файлом.
    buf = io.BytesIO(res.content)
    with tarfile.open(fileobj=buf, mode="r:gz") as tar:
        names = tar.getnames()
        assert any("inbox_state.json.bak" in n for n in names)


def test_download_endpoint_missing_snapshot(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/api/admin/snapshots/20260101T000000Z/download")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# /api/admin/snapshots/{name}/restore — 501 placeholder
# ---------------------------------------------------------------------------


def test_restore_endpoint_returns_501(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.post("/api/admin/snapshots/20260509T120000Z/restore")
    assert res.status_code == 501
    assert "not_implemented" in res.text


def test_restore_endpoint_invalid_name_rejected(
    manager: StateSnapshotManager,
) -> None:
    client = _make_client(manager)
    # `..` в имени — regex отвергнет до того как дойдёт до 501.
    res = client.post("/api/admin/snapshots/..bad../restore")
    # FastAPI может декодировать или не декодировать; принимаем 400 или 404.
    assert res.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /admin/snapshots HTML
# ---------------------------------------------------------------------------


def test_admin_page_renders_html(manager: StateSnapshotManager) -> None:
    client = _make_client(manager)
    res = client.get("/admin/snapshots")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Snapshots Admin" in res.text
    assert "fetchSnapshots" in res.text
