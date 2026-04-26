# -*- coding: utf-8 -*-
"""
Phase 2 extraction — inbox_router (Session 25).

5 read-only inbox endpoints: status, items, stale-processing, stale-open,
notifications/count. Все используют inbox_service singleton.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers.inbox_router import router as inbox_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(inbox_router)
    return TestClient(app)


def test_inbox_status_returns_summary() -> None:
    """GET /api/inbox/status → workflow snapshot."""
    fake_workflow = {"summary": {"total_items": 5}, "extra": "data"}
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.get_workflow_snapshot",
        return_value=fake_workflow,
    ):
        resp = _client().get("/api/inbox/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["summary"] == {"total_items": 5}
    assert data["workflow"] == fake_workflow


def test_inbox_items_with_query_params() -> None:
    """GET /api/inbox/items?status=open&limit=5 → list_items вызван правильно."""
    fake_items = [{"item_id": "a", "kind": "owner_request"}]
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.list_items",
        return_value=fake_items,
    ) as mock_list:
        resp = _client().get("/api/inbox/items?status=open&kind=&limit=5")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "items": fake_items}
    mock_list.assert_called_once_with(status="open", kind="", limit=5)


def test_inbox_stale_processing_response_shape() -> None:
    """GET /api/inbox/stale-processing → kind+count+items."""
    fake_items = [{"id": "x"}, {"id": "y"}]
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.list_stale_processing_items",
        return_value=fake_items,
    ):
        resp = _client().get("/api/inbox/stale-processing?kind=owner_request&limit=10")
    data = resp.json()
    assert data["ok"] is True
    assert data["kind"] == "owner_request"
    assert data["count"] == 2
    assert data["items"] == fake_items


def test_inbox_stale_open_uses_list_stale_open_items() -> None:
    """GET /api/inbox/stale-open → list_stale_open_items вызывается."""
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.list_stale_open_items",
        return_value=[],
    ) as mock_list:
        resp = _client().get("/api/inbox/stale-open?kind=test&limit=3")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    mock_list.assert_called_once_with(kind="test", limit=3)


def test_notifications_count_with_attention_severity() -> None:
    """GET /api/notifications/count → разделяет attention (error/warning) vs total."""
    fake_items = [
        {"id": "a", "severity": "info"},
        {"id": "b", "severity": "warning"},
        {"id": "c", "severity": "error"},
        {"id": "d", "severity": "info"},
    ]
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.list_items",
        return_value=fake_items,
    ):
        resp = _client().get("/api/notifications/count")
    data = resp.json()
    assert data["ok"] is True
    assert data["total"] == 4
    assert data["attention"] == 2  # warning + error


def test_notifications_count_graceful_on_exception() -> None:
    """Exception в inbox_service → graceful response, не 500."""
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.list_items",
        side_effect=RuntimeError("boom"),
    ):
        resp = _client().get("/api/notifications/count")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["total"] == 0
    assert data["attention"] == 0
    assert "boom" in data["error"]
