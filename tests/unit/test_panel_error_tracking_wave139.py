# -*- coding: utf-8 -*-
"""Wave 139: тесты forensic-логирования 5xx-ошибок owner-панели.

Покрытие:
  * ErrorEventLogger пишет JSONL за дневной файл
  * Daily rotation: keep_days=7, файлы старше — удаляются
  * Middleware ловит exception и пишет traceback
  * Middleware ловит explicit 500 response и body_sample
  * krab_owner_panel_5xx_total counter инкрементируется
  * 2xx/4xx не пишут в error log
  * Запись body_sample обрезается до 500 байт
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from src.core.owner_panel_error_tracker import ErrorEventLogger
from src.modules.web_middleware.audit_logger import (
    AuditLoggerMiddleware,
    AuditStorage,
)


def _make_storage(tmp_path: Path) -> AuditStorage:
    return AuditStorage(db_path=tmp_path / "audit.db")


def _make_error_logger(tmp_path: Path, *, today: date | None = None, keep_days: int = 7) -> ErrorEventLogger:
    fixed_day = today or date(2026, 5, 12)
    return ErrorEventLogger(
        log_dir=tmp_path / "errors",
        keep_days=keep_days,
        today_fn=lambda: fixed_day,
    )


def _make_app(storage: AuditStorage, err_logger: ErrorEventLogger) -> FastAPI:
    app = FastAPI()

    @app.get("/api/ok")
    async def ok() -> dict:
        return {"ok": True}

    @app.get("/api/boom")
    async def boom() -> dict:
        raise RuntimeError("kaboom intentional")

    @app.get("/api/explicit500")
    async def explicit() -> JSONResponse:
        return JSONResponse(
            status_code=500, content={"error": "explicit_failure", "detail": "x" * 800}
        )

    @app.get("/api/httpexc")
    async def httpexc() -> dict:
        raise HTTPException(status_code=503, detail="upstream down")

    @app.get("/api/notfound")
    async def notfound() -> JSONResponse:
        return JSONResponse(status_code=404, content={"err": "no"})

    app.add_middleware(
        AuditLoggerMiddleware, storage=storage, error_logger=err_logger
    )
    return app


def test_error_logger_writes_jsonl_record(tmp_path: Path) -> None:
    """Запись формирует JSONL с обязательными полями."""
    logger = _make_error_logger(tmp_path)
    ok = logger.record(
        method="GET",
        path="/api/x",
        status=500,
        error_class="RuntimeError",
        error_message="boom",
        traceback_text="Traceback (most recent call last):\n  RuntimeError: boom",
        body_sample=b"hello world",
        client_ip="127.0.0.1",
        auth_prefix="ab12",
    )
    assert ok is True
    records = logger.read_recent(limit=10)
    assert len(records) == 1
    row = records[0]
    assert row["method"] == "GET"
    assert row["path"] == "/api/x"
    assert row["status"] == 500
    assert row["error_class"] == "RuntimeError"
    assert row["body_sample"] == "hello world"
    assert "Traceback" in row["traceback"]
    assert row["auth_prefix"] == "ab12"


def test_error_logger_body_sample_truncated(tmp_path: Path) -> None:
    """body_sample обрезается лимитом."""
    logger = ErrorEventLogger(
        log_dir=tmp_path / "errors",
        today_fn=lambda: date(2026, 5, 12),
        body_sample_limit=50,
    )
    big_payload = ("A" * 5000).encode("utf-8")
    logger.record(method="GET", path="/api/big", status=500, body_sample=big_payload)
    rec = logger.read_recent(limit=1)[0]
    assert rec["body_sample"] is not None
    assert len(rec["body_sample"]) == 50


def test_daily_rotation_removes_files_older_than_keep_days(tmp_path: Path) -> None:
    """Файлы старше keep_days удаляются при первой записи нового дня."""
    log_dir = tmp_path / "errors"
    log_dir.mkdir()
    today = date(2026, 5, 12)
    # Создаём дневные файлы вручную: сегодня, вчера, и старше keep_days.
    for offset in [0, 1, 2, 7, 8, 30]:
        d = today - timedelta(days=offset)
        (log_dir / f"owner_panel_errors-{d.isoformat()}.jsonl").write_text("{}\n")
    logger = ErrorEventLogger(
        log_dir=log_dir, keep_days=7, today_fn=lambda: today
    )
    logger.record(method="GET", path="/api/x", status=500)

    remaining = sorted(p.name for p in log_dir.glob("owner_panel_errors-*.jsonl"))
    # cutoff = today - 7 = 2026-05-05; всё что <= этой даты — удаляется.
    # offset=7 (2026-05-05) — удаляется (== cutoff), offset=8/30 — удаляются.
    # offset=0/1/2 — остаются.
    expected_days = {today - timedelta(days=o) for o in (0, 1, 2)}
    expected_names = {f"owner_panel_errors-{d.isoformat()}.jsonl" for d in expected_days}
    assert set(remaining) == expected_names


def test_middleware_captures_exception_traceback(tmp_path: Path) -> None:
    """Exception в downstream → запись с traceback в error log."""
    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)
    app = _make_app(storage, err)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/boom")
    assert resp.status_code == 500

    records = err.read_recent(limit=10)
    assert len(records) == 1
    row = records[0]
    assert row["status"] == 500
    assert row["error_class"] == "RuntimeError"
    assert row["error_message"] is not None
    assert "kaboom" in row["error_message"]
    assert row["traceback"] is not None
    assert "RuntimeError" in row["traceback"]


def test_middleware_captures_explicit_5xx_response(tmp_path: Path) -> None:
    """Explicit 500 response → запись с body_sample, без traceback."""
    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)
    app = _make_app(storage, err)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/explicit500")
    assert resp.status_code == 500

    records = err.read_recent(limit=10)
    assert len(records) == 1
    row = records[0]
    assert row["status"] == 500
    assert row["error_class"] == "HTTPResponse"
    assert row["body_sample"] is not None
    assert "explicit_failure" in row["body_sample"]
    # traceback отсутствует у explicit response
    assert row["traceback"] is None


def test_middleware_captures_503_httpexception(tmp_path: Path) -> None:
    """HTTPException(503) → запись (FastAPI рендерит как JSONResponse 5xx)."""
    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)
    app = _make_app(storage, err)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/httpexc")
    assert resp.status_code == 503

    records = err.read_recent(limit=10)
    assert len(records) == 1
    assert records[0]["status"] == 503


def test_middleware_ignores_2xx_and_4xx(tmp_path: Path) -> None:
    """2xx/4xx не пишутся в error log."""
    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)
    app = _make_app(storage, err)
    client = TestClient(app)

    assert client.get("/api/ok").status_code == 200
    assert client.get("/api/notfound").status_code == 404

    assert err.read_recent(limit=10) == []


def test_counter_increments_on_5xx(tmp_path: Path) -> None:
    """krab_owner_panel_5xx_total инкрементируется при 5xx."""
    from src.core.metrics.audit_log import krab_owner_panel_5xx_total

    if krab_owner_panel_5xx_total is None:
        # prometheus_client недоступен — тест no-op
        return

    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)
    app = _make_app(storage, err)
    client = TestClient(app, raise_server_exceptions=False)

    before = krab_owner_panel_5xx_total.labels(
        path="/api/boom", error_class="RuntimeError"
    )._value.get()  # type: ignore[attr-defined]

    client.get("/api/boom")

    after = krab_owner_panel_5xx_total.labels(
        path="/api/boom", error_class="RuntimeError"
    )._value.get()  # type: ignore[attr-defined]
    assert after == before + 1


def test_exempt_path_skipped(tmp_path: Path) -> None:
    """Exempt-путь (/metrics) минует и audit storage, и error log даже при 5xx.

    Wave 139: error tracker не должен срабатывать для high-frequency
    monitoring endpoints — иначе forensic log захлёбывается шумом.
    """
    storage = _make_storage(tmp_path)
    err = _make_error_logger(tmp_path)

    app = FastAPI()

    @app.get("/metrics")
    async def metrics_5xx() -> JSONResponse:
        # симулируем 5xx на exempt-пути: middleware не должна его перехватить.
        return JSONResponse(status_code=500, content={"err": "should-be-skipped"})

    app.add_middleware(AuditLoggerMiddleware, storage=storage, error_logger=err)
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/metrics")
    assert resp.status_code == 500

    # Ни audit storage, ни error log не должны видеть exempt path.
    assert storage.query_recent(limit=10) == []
    assert err.read_recent(limit=10) == []


def test_env_gate_off_skips_middleware_attachment(
    tmp_path: Path, monkeypatch
) -> None:
    """KRAB_OWNER_PANEL_AUDIT_ENABLED=0 → is_audit_log_enabled() возвращает False.

    Когда env-gate off, web_app._maybe_install_audit_middleware не подключает
    middleware вовсе → error tracker не активен (no-op). Проверяем именно
    флаг, т.к. attachment-флоу контролирует WebApp на старте.
    """
    from src.modules.web_middleware.audit_logger import is_audit_log_enabled

    monkeypatch.setenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", "0")
    assert is_audit_log_enabled() is False

    monkeypatch.setenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", "1")
    assert is_audit_log_enabled() is True

    monkeypatch.delenv("KRAB_OWNER_PANEL_AUDIT_ENABLED", raising=False)
    # default-ON: отсутствие переменной = enabled.
    assert is_audit_log_enabled() is True

    # Sanity: пустой ErrorEventLogger в этом случае не получает событий,
    # т.к. middleware не подключена. Симулируем "no middleware" сценарий
    # — без AuditLoggerMiddleware error log остаётся пустым.
    storage = _make_storage(tmp_path)  # noqa: F841 (только для покрытия фикстуры)
    err = _make_error_logger(tmp_path)
    app = FastAPI()

    @app.get("/api/boom")
    async def boom() -> dict:
        raise RuntimeError("kaboom")

    # Намеренно НЕ добавляем middleware (имитируем env=0 поведение web_app).
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/api/boom")
    assert err.read_recent(limit=10) == []
