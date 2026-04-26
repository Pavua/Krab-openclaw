# -*- coding: utf-8 -*-
"""
Phase 2 extraction — inbox_router (Session 25).

Wave 3: 5 read-only inbox endpoints.
Wave O: factory pattern + 4 POST endpoints (update / stale-*/remediate / create)
с auth-проверкой через ``ctx.assert_write_access``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.inbox_router import build_inbox_router


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_inbox_router(ctx or _build_ctx()))
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET endpoints (Wave 3 — adapted to factory pattern)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# POST /api/inbox/update (Wave O)
# ---------------------------------------------------------------------------


def test_inbox_update_set_status_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/inbox/update со status=done → set_item_status вызывается."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.set_item_status",
        return_value={"ok": True, "item": {"id": "x"}},
    ) as mock_set:
        resp = _client().post(
            "/api/inbox/update",
            json={"item_id": "x", "status": "done", "actor": "owner-ui"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["ok"] is True
    mock_set.assert_called_once()


def test_inbox_update_resolve_approval_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/inbox/update со status=approved → resolve_approval вызывается."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.resolve_approval",
        return_value={"ok": True},
    ) as mock_resolve:
        resp = _client().post(
            "/api/inbox/update",
            json={"item_id": "x", "status": "approved"},
        )
    assert resp.status_code == 200
    mock_resolve.assert_called_once()
    assert mock_resolve.call_args.kwargs["approved"] is True


def test_inbox_update_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, заголовок не передан → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post("/api/inbox/update", json={"item_id": "x", "status": "done"})
    assert resp.status_code == 403


def test_inbox_update_empty_item_id_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой item_id → 400 inbox_empty_item_id."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post("/api/inbox/update", json={"item_id": "", "status": "done"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "inbox_empty_item_id"


def test_inbox_update_not_found_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """inbox_service возвращает {ok:False} → 404."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.set_item_status",
        return_value={"ok": False, "error": "inbox_item_not_found"},
    ):
        resp = _client().post(
            "/api/inbox/update",
            json={"item_id": "missing", "status": "done"},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/inbox/stale-processing/remediate
# ---------------------------------------------------------------------------


def test_stale_processing_remediate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST stale-processing/remediate → bulk_update_status вызывается."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_items = [{"item_id": "a"}, {"item_id": "b"}]
    with (
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.list_stale_processing_items",
            return_value=fake_items,
        ),
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.bulk_update_status",
            return_value={"ok": True, "updated": 2},
        ) as mock_bulk,
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.get_workflow_snapshot",
            return_value={"summary": {"total": 0}},
        ),
    ):
        resp = _client().post(
            "/api/inbox/stale-processing/remediate",
            json={"kind": "owner_request", "status": "cancelled"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["count"] == 2
    mock_bulk.assert_called_once()


def test_stale_processing_remediate_invalid_status_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """status не done/cancelled → 400."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post(
        "/api/inbox/stale-processing/remediate",
        json={"status": "approved"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "inbox_invalid_bulk_stale_status"


def test_stale_processing_remediate_invalid_auth_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, без header → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post("/api/inbox/stale-processing/remediate", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/inbox/stale-open/remediate
# ---------------------------------------------------------------------------


def test_stale_open_remediate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST stale-open/remediate → list_stale_open_items + bulk_update."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_items = [{"item_id": "z"}]
    with (
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.list_stale_open_items",
            return_value=fake_items,
        ) as mock_list,
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.bulk_update_status",
            return_value={"ok": True},
        ),
        patch(
            "src.modules.web_routers.inbox_router.inbox_service.get_workflow_snapshot",
            return_value={"summary": {}},
        ),
    ):
        resp = _client().post(
            "/api/inbox/stale-open/remediate",
            json={"kind": "owner_request"},
        )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    mock_list.assert_called_once()


def test_stale_open_remediate_invalid_auth_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, без header → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post("/api/inbox/stale-open/remediate", json={})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/inbox/create
# ---------------------------------------------------------------------------


def test_inbox_create_owner_task_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/inbox/create kind=owner_task → upsert_owner_task."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    with patch(
        "src.modules.web_routers.inbox_router.inbox_service.upsert_owner_task",
        return_value={"ok": True, "item_id": "new-1"},
    ) as mock_upsert:
        resp = _client().post(
            "/api/inbox/create",
            json={
                "kind": "owner_task",
                "title": "T",
                "body": "B",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_upsert.assert_called_once()


def test_inbox_create_invalid_kind_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """kind=foo → 400 inbox_create_invalid_kind."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post(
        "/api/inbox/create",
        json={"kind": "foo", "title": "T", "body": "B"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "inbox_create_invalid_kind"


def test_inbox_create_missing_title_body_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустые title/body → 400."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post(
        "/api/inbox/create",
        json={"kind": "owner_task", "title": "", "body": ""},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "inbox_create_title_body_required"


def test_inbox_create_invalid_auth_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """WEB_API_KEY установлен, без header → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post(
        "/api/inbox/create",
        json={"kind": "owner_task", "title": "T", "body": "B"},
    )
    assert resp.status_code == 403
