# -*- coding: utf-8 -*-
"""
Inbox router — Phase 2 extraction (Session 25).

Read-only inbox endpoints — все используют ``inbox_service`` singleton
без доступа к WebApp instance state.

POST endpoints (`/api/inbox/update`, `/api/inbox/create`) и
remediate endpoints — отложены до RouterContext extraction (нужен
``_assert_write_access``).

Read-only endpoints:
- /api/inbox/status — workflow summary
- /api/inbox/items — фильтрованный список
- /api/inbox/stale-processing — stale acked item-ы
- /api/inbox/stale-open — стартые open item-ы
- /api/notifications/count — badge counter для UI
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ...core.inbox_service import inbox_service

router = APIRouter(tags=["inbox"])


@router.get("/api/inbox/status")
async def inbox_status() -> dict:
    """Persisted summary owner-visible inbox/escalation слоя."""
    workflow = inbox_service.get_workflow_snapshot()
    return {
        "ok": True,
        "summary": workflow.get("summary") or {},
        "workflow": workflow,
    }


@router.get("/api/inbox/items")
async def inbox_items(
    status: str = Query(default="open"),
    kind: str = Query(default=""),
    limit: int = Query(default=20),
) -> dict:
    """Inbox items с простыми фильтрами для owner UI/API."""
    return {
        "ok": True,
        "items": inbox_service.list_items(status=status, kind=kind, limit=limit),
    }


@router.get("/api/inbox/stale-processing")
async def inbox_stale_processing(
    kind: str = Query(default="owner_request"),
    limit: int = Query(default=20),
) -> dict:
    """Stale `acked` item-ы для owner remediation runbook."""
    items = inbox_service.list_stale_processing_items(kind=kind, limit=limit)
    return {
        "ok": True,
        "kind": str(kind or "").strip().lower(),
        "count": len(items),
        "items": items,
    }


@router.get("/api/inbox/stale-open")
async def inbox_stale_open(
    kind: str = Query(default="owner_request"),
    limit: int = Query(default=20),
) -> dict:
    """Старые `open` item-ы для owner remediation runbook."""
    items = inbox_service.list_stale_open_items(kind=kind, limit=limit)
    return {
        "ok": True,
        "kind": str(kind or "").strip().lower(),
        "count": len(items),
        "items": items,
    }


@router.get("/api/notifications/count")
async def notification_count() -> dict:
    """Количество уведомлений для badge в UI."""
    try:
        items = inbox_service.list_items(status="open", limit=100)
        attention = [i for i in items if i.get("severity") in ("error", "warning")]
        return {"ok": True, "total": len(items), "attention": len(attention)}
    except Exception as exc:  # noqa: BLE001 — graceful badge не должен падать
        return {"ok": False, "total": 0, "attention": 0, "error": str(exc)[:80]}
