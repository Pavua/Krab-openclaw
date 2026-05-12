# -*- coding: utf-8 -*-
"""
Unit tests for ``src.modules.web_routers.inbox_admin_router`` — Wave 157.

Покрывает:
- GET /api/admin/inbox/dashboard — shape, stats, kinds, items
- Filter params (status/kind/limit) проходят в inbox_service
- _annotate_item: age_hours, is_stale, actions для разных статусов
- GET /admin/inbox — HTML render
- Graceful degradation при ошибках service
- Validation: limit вне диапазона (FastAPI Query bounds)

Используется чистый FastAPI + TestClient, без полного WebApp.
``inbox_service`` patched через ``unittest.mock.patch``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.inbox_admin_router import (
    _annotate_item,
    _compute_age_hours,
    build_inbox_admin_router,
)

# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeInboxService:
    """Stub ``inbox_service`` singleton — записывает list_items вызовы."""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        stale_open: list[dict[str, Any]] | None = None,
        all_items: list[dict[str, Any]] | None = None,
    ) -> None:
        self._items = items or []
        self._all_items = all_items if all_items is not None else self._items
        self._summary = summary or {}
        self._stale_open = stale_open or []
        self.list_items_calls: list[dict[str, Any]] = []

    def list_items(
        self,
        *,
        status: str = "",
        kind: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.list_items_calls.append({"status": status, "kind": kind, "limit": limit})
        # Для status="all" возвращаем все, иначе фильтруем по статусу.
        normalized = str(status or "").strip().lower()
        source = self._all_items if normalized == "all" else self._items
        rows: list[dict[str, Any]] = []
        for it in source:
            if normalized and normalized != "all":
                item_status = str(it.get("status") or "").lower()
                if normalized == "open" and item_status != "open":
                    continue
                if normalized != "open" and item_status != normalized:
                    continue
            if kind and str(it.get("kind") or "") != kind:
                continue
            rows.append(it)
            if len(rows) >= max(1, int(limit or 20)):
                break
        return rows

    def list_stale_open_items(self, *, kind: str = "", limit: int = 20) -> list[dict[str, Any]]:
        return list(self._stale_open)

    def get_workflow_snapshot(self) -> dict[str, Any]:
        return {"summary": dict(self._summary)}


def _build_ctx() -> RouterContext:
    """Минимальный RouterContext для tests."""
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client(svc: _FakeInboxService | None = None) -> tuple[TestClient, _FakeInboxService]:
    """Возвращает (TestClient, svc) с patched inbox_service singleton."""
    fake = svc or _FakeInboxService()
    app = FastAPI()
    app.include_router(build_inbox_admin_router(_build_ctx()))
    return TestClient(app), fake


def _patch_svc(svc: _FakeInboxService):
    return patch("src.core.inbox_service.inbox_service", svc)


def _make_item(
    *,
    item_id: str = "item_test_1",
    kind: str = "owner_request",
    status: str = "open",
    severity: str = "info",
    title: str = "Test item",
    age_hours: float = 1.0,
) -> dict[str, Any]:
    """Создаёт raw inbox item dict с реалистичным created_at_utc."""
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return {
        "item_id": item_id,
        "kind": kind,
        "status": status,
        "severity": severity,
        "title": title,
        "body": "body",
        "created_at_utc": created.isoformat().replace("+00:00", "Z"),
        "updated_at_utc": created.isoformat().replace("+00:00", "Z"),
    }


# ── Helper tests ────────────────────────────────────────────────────────────


def test_compute_age_hours_returns_zero_on_empty() -> None:
    """Пустой/невалидный timestamp → 0.0 без exception."""
    assert _compute_age_hours("") == 0.0
    assert _compute_age_hours("not-a-date") == 0.0


def test_compute_age_hours_naive_datetime_treated_as_utc() -> None:
    """ISO без tz должен парситься (assume UTC)."""
    naive = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    age = _compute_age_hours(naive)
    assert 1.5 < age < 2.5, f"expected ~2.0, got {age}"


def test_annotate_item_marks_stale_for_old_open_item() -> None:
    """Open item старше 12h помечен is_stale=True, actions=[ack,done,cancel]."""
    item = _make_item(status="open", age_hours=20.0)
    annotated = _annotate_item(item)
    assert annotated["is_stale"] is True
    assert annotated["age_hours"] > 12.0
    assert annotated["actions"] == ["ack", "done", "cancel"]


def test_annotate_item_acked_has_reduced_actions() -> None:
    """Acked item — actions=[done, cancel], is_stale=False (не open)."""
    item = _make_item(status="acked", age_hours=24.0)
    annotated = _annotate_item(item)
    assert annotated["is_stale"] is False
    assert annotated["actions"] == ["done", "cancel"]


def test_annotate_item_done_has_no_actions() -> None:
    """Финальные статусы (done/cancelled) — actions=[]."""
    for st in ("done", "cancelled"):
        item = _make_item(status=st, age_hours=1.0)
        annotated = _annotate_item(item)
        assert annotated["actions"] == [], f"status={st} should have empty actions"
        assert annotated["is_stale"] is False


# ── GET /api/admin/inbox/dashboard tests ────────────────────────────────────


def test_dashboard_returns_full_shape() -> None:
    """Базовая форма: ok, stats, kinds, items, filter, now."""
    svc = _FakeInboxService(
        items=[
            _make_item(item_id="a", status="open"),
            _make_item(item_id="b", status="open", severity="warning"),
        ],
        summary={"open": 2, "acked": 0, "done": 5, "cancelled": 1},
    )
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "stats" in data and "kinds" in data and "items" in data
    assert data["filter"]["status"] == "open"
    assert "now" in data
    # Items аннотированы
    for it in data["items"]:
        assert "age_hours" in it and "is_stale" in it and "actions" in it


def test_dashboard_stats_from_summary() -> None:
    """Stats берут open/acked/done/cancelled из summary."""
    svc = _FakeInboxService(
        items=[_make_item(severity="error")],
        summary={"open": 10, "acked": 3, "done": 50, "cancelled": 2},
        stale_open=[_make_item(item_id="s1", age_hours=20.0)],
    )
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard")
    stats = resp.json()["stats"]
    assert stats["total_open"] == 10
    assert stats["acked"] == 3
    assert stats["done"] == 50
    assert stats["cancelled"] == 2
    assert stats["stale_open"] == 1
    # Attention = severity warning/error среди open
    assert stats["attention"] == 1


def test_dashboard_filters_pass_through() -> None:
    """status/kind/limit query params передаются в list_items."""
    svc = _FakeInboxService()
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get(
            "/api/admin/inbox/dashboard",
            params={"status": "acked", "kind": "proactive_action", "limit": 25},
        )
    assert resp.status_code == 200
    # Первый вызов — items под фильтр
    first_call = svc.list_items_calls[0]
    assert first_call["status"] == "acked"
    assert first_call["kind"] == "proactive_action"
    assert first_call["limit"] == 25


def test_dashboard_kinds_breakdown_sorted_by_open_desc() -> None:
    """kinds breakdown сортирован по open DESC (max-открытых сверху)."""
    svc = _FakeInboxService(
        items=[
            _make_item(item_id="o1", kind="alpha", status="open"),
            _make_item(item_id="o2", kind="alpha", status="open"),
            _make_item(item_id="o3", kind="beta", status="open"),
        ],
        all_items=[
            _make_item(item_id="o1", kind="alpha", status="open"),
            _make_item(item_id="o2", kind="alpha", status="open"),
            _make_item(item_id="o3", kind="beta", status="open"),
            _make_item(item_id="a1", kind="beta", status="acked"),
        ],
    )
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard")
    kinds = resp.json()["kinds"]
    # alpha (2 open) перед beta (1 open)
    assert kinds[0]["kind"] == "alpha"
    assert kinds[0]["open"] == 2
    assert kinds[1]["kind"] == "beta"
    assert kinds[1]["open"] == 1
    assert kinds[1]["acked"] == 1


def test_dashboard_validates_limit_bounds() -> None:
    """limit < 1 или > 200 → 422 (FastAPI Query bounds)."""
    svc = _FakeInboxService()
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard", params={"limit": 0})
        assert resp.status_code == 422
        resp = client.get("/api/admin/inbox/dashboard", params={"limit": 500})
        assert resp.status_code == 422


def test_dashboard_handles_service_failure_gracefully() -> None:
    """Если list_items бросает Exception → 500 с inbox_list_failed detail."""

    class _BrokenSvc(_FakeInboxService):
        def list_items(self, **_kw: Any) -> list[dict[str, Any]]:
            raise RuntimeError("boom")

    svc = _BrokenSvc()
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard")
    assert resp.status_code == 500
    assert "inbox_list_failed" in resp.json()["detail"]


def test_dashboard_partial_failures_in_stats_dont_crash() -> None:
    """Сбой get_workflow_snapshot не должен валить весь endpoint — defaults."""

    class _PartialSvc(_FakeInboxService):
        def get_workflow_snapshot(self) -> dict[str, Any]:
            raise RuntimeError("snapshot broken")

    svc = _PartialSvc(items=[_make_item(severity="warning")])
    client, _ = _client(svc)
    with _patch_svc(svc):
        resp = client.get("/api/admin/inbox/dashboard")
    # Endpoint должен ответить с дефолтными stats — snapshot падает, но
    # list_items работает.
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # attention считается через list_items напрямую — должно быть 1
    assert data["stats"]["attention"] == 1


# ── GET /admin/inbox tests ──────────────────────────────────────────────────


def test_admin_inbox_page_renders_html() -> None:
    """GET /admin/inbox возвращает HTMLResponse 200."""
    svc = _FakeInboxService()
    client, _ = _client(svc)
    resp = client.get("/admin/inbox")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "Krab" in body and "Inbox" in body
    # Sanity: содержит fetch /api/admin/inbox/dashboard
    assert "/api/admin/inbox/dashboard" in body
    # Bulk actions buttons присутствуют
    assert "btn-ack-stale" in body
    assert "btn-cleanup-stale" in body
    assert "Cache-Control" in resp.headers and "no-store" in resp.headers["Cache-Control"]


def test_admin_inbox_page_contains_filter_controls() -> None:
    """HTML содержит filter bar (status select, kind input, limit input)."""
    svc = _FakeInboxService()
    client, _ = _client(svc)
    resp = client.get("/admin/inbox")
    body = resp.text
    assert 'id="f-status"' in body
    assert 'id="f-kind"' in body
    assert 'id="f-limit"' in body
    # nav tab Inbox active
    assert 'class="active">Inbox' in body
