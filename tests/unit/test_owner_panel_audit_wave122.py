# -*- coding: utf-8 -*-
"""Wave 122: тесты owner-panel audit log middleware.

Покрываем:
    * запись row при обычном request
    * skip exempt path (/metrics, /health)
    * query_recent возвращает в DESC порядке
    * env-gate is_audit_log_enabled (default-ON, =0 off)
    * auth_prefix extraction из Authorization / X-Krab-Web-Key
    * client_ip extraction с X-Forwarded-For
    * classify_status для metrics
    * middleware пишет 500 при exception в downstream
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from src.core.metrics.audit_log import classify_status
from src.modules.web_middleware.audit_logger import (
    EXEMPT_PATHS,
    AuditLoggerMiddleware,
    AuditStorage,
    is_audit_log_enabled,
)


def _make_storage(tmp_path: Path) -> AuditStorage:
    db = tmp_path / "audit.db"
    return AuditStorage(db_path=db)


def _make_app(storage: AuditStorage) -> FastAPI:
    app = FastAPI()

    @app.get("/api/foo")
    async def foo() -> dict:
        return {"ok": True}

    @app.get("/api/boom")
    async def boom() -> dict:
        raise RuntimeError("intentional")

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse("# fake")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    app.add_middleware(AuditLoggerMiddleware, storage=storage)
    return app


def test_request_recorded(tmp_path: Path) -> None:
    """Обычный GET /api/foo пишет одну строку с status=200."""
    storage = _make_storage(tmp_path)
    client = TestClient(_make_app(storage))
    resp = client.get("/api/foo")
    assert resp.status_code == 200
    rows = storage.query_recent(limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["method"] == "GET"
    assert row["path"] == "/api/foo"
    assert row["status"] == 200
    assert row["duration_ms"] >= 0.0


def test_exempt_paths_not_recorded(tmp_path: Path) -> None:
    """/metrics и /health не должны попадать в audit log."""
    storage = _make_storage(tmp_path)
    client = TestClient(_make_app(storage))
    client.get("/metrics")
    client.get("/health")
    rows = storage.query_recent(limit=10)
    assert rows == []
    assert "/metrics" in EXEMPT_PATHS
    assert "/health" in EXEMPT_PATHS


def test_auth_prefix_from_header(tmp_path: Path) -> None:
    """Authorization Bearer → первые 4 символа токена в auth_prefix."""
    storage = _make_storage(tmp_path)
    client = TestClient(_make_app(storage))
    client.get("/api/foo", headers={"Authorization": "Bearer abcdef123456"})
    rows = storage.query_recent(limit=1)
    assert rows[0]["auth_prefix"] == "abcd"


def test_auth_prefix_from_x_krab_web_key(tmp_path: Path) -> None:
    """X-Krab-Web-Key → первые 4 символа в auth_prefix."""
    storage = _make_storage(tmp_path)
    client = TestClient(_make_app(storage))
    client.get("/api/foo", headers={"X-Krab-Web-Key": "ZZZZyyyy11"})
    rows = storage.query_recent(limit=1)
    assert rows[0]["auth_prefix"] == "ZZZZ"


def test_client_ip_from_xff(tmp_path: Path) -> None:
    """X-Forwarded-For имеет приоритет над request.client.host."""
    storage = _make_storage(tmp_path)
    client = TestClient(_make_app(storage))
    client.get("/api/foo", headers={"X-Forwarded-For": "8.8.8.8, 10.0.0.1"})
    rows = storage.query_recent(limit=1)
    assert rows[0]["client_ip"] == "8.8.8.8"


def test_query_recent_desc_order(tmp_path: Path) -> None:
    """query_recent возвращает строки в порядке убывания ts_unix."""
    storage = _make_storage(tmp_path)
    storage.record(
        ts_unix=1000.0,
        method="GET",
        path="/api/a",
        status=200,
        auth_prefix=None,
        client_ip=None,
        duration_ms=1.0,
    )
    storage.record(
        ts_unix=2000.0,
        method="GET",
        path="/api/b",
        status=200,
        auth_prefix=None,
        client_ip=None,
        duration_ms=1.0,
    )
    storage.record(
        ts_unix=1500.0,
        method="GET",
        path="/api/c",
        status=200,
        auth_prefix=None,
        client_ip=None,
        duration_ms=1.0,
    )
    rows = storage.query_recent(limit=10)
    assert [r["path"] for r in rows] == ["/api/b", "/api/c", "/api/a"]


def test_env_gate_default_on_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_OWNER_PANEL_AUDIT_ENABLED default-ON; =0 отключает."""
    monkeypatch.delenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", raising=False)
    assert is_audit_log_enabled() is True
    monkeypatch.setenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", "0")
    assert is_audit_log_enabled() is False
    monkeypatch.setenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", "1")
    assert is_audit_log_enabled() is True


def test_classify_status() -> None:
    """classify_status корректно мапит код → класс."""
    assert classify_status(200) == "2xx"
    assert classify_status(201) == "2xx"
    assert classify_status(302) == "3xx"
    assert classify_status(404) == "4xx"
    assert classify_status(429) == "4xx"
    assert classify_status(500) == "5xx"
    assert classify_status(599) == "5xx"
    assert classify_status(100) == "other"
    assert classify_status(700) == "other"


def test_exception_in_downstream_recorded_as_500(tmp_path: Path) -> None:
    """Если downstream raise'ит — пишем 500 и пробрасываем дальше."""
    storage = _make_storage(tmp_path)
    app = _make_app(storage)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/boom")
    assert resp.status_code == 500
    rows = storage.query_recent(limit=1)
    assert rows[0]["path"] == "/api/boom"
    assert rows[0]["status"] == 500
