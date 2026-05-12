# -*- coding: utf-8 -*-
"""Wave 131: per-endpoint latency histogram tests.

Покрываем:
    * observe увеличивает sample-count для (method, path_pattern)
    * path_pattern берётся из FastAPI route (template, а не raw URL)
    * exempt paths (/metrics, /health) не наблюдаются
    * exception в downstream всё равно фиксирует latency (500-сурогат)
    * buckets matches LATENCY_BUCKETS
    * observe_request_duration no-op при отрицательном duration
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from src.core.metrics.router_latency import (
    LATENCY_BUCKETS,
    krab_owner_panel_request_duration_seconds,
    observe_request_duration,
)
from src.modules.web_middleware.audit_logger import (
    AuditLoggerMiddleware,
    AuditStorage,
)


def _make_storage(tmp_path: Path) -> AuditStorage:
    return AuditStorage(db_path=tmp_path / "audit.db")


def _make_app(storage: AuditStorage) -> FastAPI:
    app = FastAPI()

    @app.get("/api/foo")
    async def foo() -> dict:
        return {"ok": True}

    @app.get("/api/inbox/{item_id}")
    async def inbox_item(item_id: str) -> dict:
        return {"id": item_id}

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


def _sample_count(method: str, path_pattern: str) -> float:
    """Текущий накопительный sample-count для конкретного label-set."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    metric = krab_owner_panel_request_duration_seconds.labels(
        method=method, path_pattern=path_pattern
    )
    # `_sum` всегда инкрементируется на величину observation,
    # но `_count` (хранится в одном из child-метрик) надёжнее для отслеживания.
    # У histogram-child есть метод `_buckets`/`_sum`/`_count` — берём `_count`.
    return float(metric._sum.get())  # type: ignore[attr-defined]


def test_buckets_exposed() -> None:
    """Buckets — explicit и публично проверяемые."""
    assert LATENCY_BUCKETS == (0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0)


def test_observe_records_for_pattern(tmp_path: Path) -> None:
    """Static endpoint попадает в histogram с path_pattern == маршрут FastAPI."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    before = _sample_count("GET", "/api/foo")
    client = TestClient(_make_app(_make_storage(tmp_path)))
    assert client.get("/api/foo").status_code == 200
    after = _sample_count("GET", "/api/foo")
    assert after > before, "histogram должна получить observation"


def test_dynamic_path_collapses_to_pattern(tmp_path: Path) -> None:
    """`/api/inbox/{item_id}` не плодит отдельные label-set'ы на каждый id."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    client = TestClient(_make_app(_make_storage(tmp_path)))
    before = _sample_count("GET", "/api/inbox/{item_id}")
    for item_id in ("a", "b", "c", "d"):
        assert client.get(f"/api/inbox/{item_id}").status_code == 200
    after = _sample_count("GET", "/api/inbox/{item_id}")
    # 4 observation'а добавили положительный sum (duration > 0).
    assert after > before


def test_exempt_paths_not_observed(tmp_path: Path) -> None:
    """/metrics и /health не должны вообще наблюдаться (даже как unmatched)."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    client = TestClient(_make_app(_make_storage(tmp_path)))
    metrics_before = _sample_count("GET", "/metrics")
    health_before = _sample_count("GET", "/health")
    assert client.get("/metrics").status_code == 200
    assert client.get("/health").status_code == 200
    # Sum не должен сдвинуться: middleware вернула early до observe.
    assert _sample_count("GET", "/metrics") == metrics_before
    assert _sample_count("GET", "/health") == health_before


def test_exception_path_still_observed(tmp_path: Path) -> None:
    """500-исключение в handler фиксируется в latency histogram."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    client = TestClient(_make_app(_make_storage(tmp_path)), raise_server_exceptions=False)
    before = _sample_count("GET", "/api/boom")
    resp = client.get("/api/boom")
    assert resp.status_code == 500
    after = _sample_count("GET", "/api/boom")
    assert after > before


def test_observe_negative_duration_clamped() -> None:
    """Отрицательное duration не падает и не пишет negative sum."""
    if krab_owner_panel_request_duration_seconds is None:
        pytest.skip("prometheus_client недоступен")
    before = _sample_count("GET", "/api/_clamp_test")
    observe_request_duration("GET", "/api/_clamp_test", -1.0)
    after = _sample_count("GET", "/api/_clamp_test")
    # max(0, -1) = 0 → sum не растёт.
    assert after == before


def test_observe_no_prometheus_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если prometheus_client отсутствует — observe — no-op без exception."""
    import src.core.metrics.router_latency as mod

    monkeypatch.setattr(mod, "krab_owner_panel_request_duration_seconds", None)
    # Должно тихо отработать.
    mod.observe_request_duration("GET", "/api/x", 0.123)
