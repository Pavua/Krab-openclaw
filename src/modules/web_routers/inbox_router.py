# -*- coding: utf-8 -*-
"""
Inbox router — Phase 2 extraction (Session 25).

Wave 3: 5 read-only inbox endpoints (`router` direct).
Wave O: converted в factory-pattern + добавлены 4 POST endpoints с
``ctx.assert_write_access`` для auth-проверки.

Read-only endpoints:
- GET /api/inbox/status — workflow summary
- GET /api/inbox/items — фильтрованный список
- GET /api/inbox/stale-processing — stale acked item-ы
- GET /api/inbox/stale-open — старые open item-ы
- GET /api/notifications/count — badge counter для UI

Write endpoints (Wave O):
- POST /api/inbox/update — set_item_status / resolve_approval
- POST /api/inbox/stale-processing/remediate — bulk-action stale acked
- POST /api/inbox/stale-open/remediate — bulk-action stale open
- POST /api/inbox/create — owner_task / approval_request
- POST /api/inbox/create-vpn-alert — VPN watchdog → inbox bridge (kind=vpn_alert)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ...core.inbox_service import inbox_service
from ._context import RouterContext


def build_inbox_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с inbox-endpoints (GET + POST)."""
    router = APIRouter(tags=["inbox"])

    # ---------------------------------------------------------------------
    # GET endpoints (Wave 3)
    # ---------------------------------------------------------------------

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
        kind: str = Query(default=""),
        limit: int = Query(default=20),
    ) -> dict:
        """
        Старые `open` item-ы для owner remediation runbook.

        Без параметра ``kind`` возвращает stale-open items по ВСЕМ kinds —
        чтобы соответствовать агрегатному счётчику ``stale_open_items`` в
        ``/api/health/lite``. Передача ``?kind=owner_request`` (или другого)
        фильтрует список как раньше — backward-compat сохранён.
        """
        items = inbox_service.list_stale_open_items(kind=kind, limit=limit)
        normalized_kind = str(kind or "").strip().lower()
        return {
            "ok": True,
            "kind": normalized_kind or "all",
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

    # ---------------------------------------------------------------------
    # POST endpoints (Wave O)
    # ---------------------------------------------------------------------

    @router.post("/api/inbox/update")
    async def inbox_update(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Позволяет owner UI подтверждать или закрывать inbox item."""
        ctx.assert_write_access(x_krab_web_key, token)
        item_id = str(payload.get("item_id") or "").strip()
        status = str(payload.get("status") or "").strip().lower()
        note = str(payload.get("note") or "").strip()
        actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
        if not item_id:
            raise HTTPException(status_code=400, detail="inbox_empty_item_id")
        try:
            if status in {"approved", "rejected"}:
                result = inbox_service.resolve_approval(
                    item_id,
                    approved=(status == "approved"),
                    actor=actor,
                    note=note,
                )
            else:
                result = inbox_service.set_item_status(
                    item_id,
                    status=status,
                    actor=actor,
                    note=note,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result.get("ok"):
            error = str(result.get("error") or "inbox_item_not_found")
            if error == "inbox_item_not_approval":
                raise HTTPException(status_code=400, detail=error)
            raise HTTPException(status_code=404, detail=error)
        return {
            "ok": True,
            "result": result,
        }

    @router.post("/api/inbox/stale-processing/remediate")
    async def inbox_stale_processing_remediate(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Выполняет безопасный bulk-action только по реально stale `acked` item-ам.

        Endpoint намеренно ограничен финальными статусами `done/cancelled`,
        чтобы owner UI не мог случайно массово прогнать небезопасные
        approval- или произвольные status-переходы.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        kind = str(payload.get("kind") or "owner_request").strip().lower() or "owner_request"
        final_status = str(payload.get("status") or "cancelled").strip().lower() or "cancelled"
        note = str(payload.get("note") or "").strip()
        actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
        limit = max(1, min(int(payload.get("limit") or 20), 50))
        if final_status not in {"done", "cancelled"}:
            raise HTTPException(status_code=400, detail="inbox_invalid_bulk_stale_status")

        stale_items = inbox_service.list_stale_processing_items(kind=kind, limit=limit)
        result = inbox_service.bulk_update_status(
            item_ids=[str(item.get("item_id") or "").strip() for item in stale_items],
            status=final_status,
            actor=actor,
            note=note or f"bulk_stale_processing_{final_status}",
        )
        if not result.get("ok"):
            error = str(result.get("error") or "inbox_bulk_stale_remediation_failed")
            raise HTTPException(status_code=400, detail=error)
        workflow = inbox_service.get_workflow_snapshot()
        return {
            "ok": True,
            "kind": kind,
            "status": final_status,
            "count": len(stale_items),
            "items": stale_items,
            "result": result,
            "summary": workflow.get("summary") or {},
        }

    @router.post("/api/inbox/stale-open/remediate")
    async def inbox_stale_open_remediate(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Выполняет безопасный bulk-action только по реально старым `open` item-ам.

        Нужен для legacy-open owner_request/mention, которые уже нельзя
        считать fresh inbox, но которые не ушли в processing.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        kind = str(payload.get("kind") or "owner_request").strip().lower() or "owner_request"
        final_status = str(payload.get("status") or "cancelled").strip().lower() or "cancelled"
        note = str(payload.get("note") or "").strip()
        actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
        limit = max(1, min(int(payload.get("limit") or 20), 50))
        if final_status not in {"done", "cancelled"}:
            raise HTTPException(status_code=400, detail="inbox_invalid_bulk_stale_open_status")

        stale_items = inbox_service.list_stale_open_items(kind=kind, limit=limit)
        result = inbox_service.bulk_update_status(
            item_ids=[str(item.get("item_id") or "").strip() for item in stale_items],
            status=final_status,
            actor=actor,
            note=note or f"bulk_stale_open_{final_status}",
        )
        if not result.get("ok"):
            error = str(result.get("error") or "inbox_bulk_stale_open_remediation_failed")
            raise HTTPException(status_code=400, detail=error)
        workflow = inbox_service.get_workflow_snapshot()
        return {
            "ok": True,
            "kind": kind,
            "status": final_status,
            "count": len(stale_items),
            "items": stale_items,
            "result": result,
            "summary": workflow.get("summary") or {},
        }

    @router.post("/api/inbox/bulk-ack-stale")
    async def inbox_bulk_ack_stale(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Массово ack-ает stale `open` items по фильтрам kind/severity/age.

        Создан как ответ на ситуацию: Agent S починил dedupe для новых cron
        events, но старые `proactive_action` продолжают висеть. Endpoint
        даёт безопасный bulk-cleanup без поштучного `set_item_status`.
        """
        ctx.assert_write_access(x_krab_web_key, token)
        kind = payload.get("kind")
        severity = payload.get("severity")
        try:
            age_threshold_hours = int(payload.get("age_threshold_hours") or 12)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="inbox_invalid_age_threshold") from exc
        dry_run = bool(payload.get("dry_run", False))
        actor = str(payload.get("actor") or "owner-ui").strip().lower() or "owner-ui"
        note = str(payload.get("note") or "").strip()
        target_status = str(payload.get("target_status") or "acked").strip().lower() or "acked"
        try:
            result = inbox_service.bulk_acknowledge_stale(
                kind=str(kind).strip() if kind else None,
                severity=str(severity).strip() if severity else None,
                age_threshold_hours=age_threshold_hours,
                dry_run=dry_run,
                actor=actor,
                note=note,
                target_status=target_status,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "result": result}

    @router.post("/api/inbox/create")
    async def inbox_create(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Позволяет owner UI создавать owner-task или approval-request."""
        ctx.assert_write_access(x_krab_web_key, token)
        kind = str(payload.get("kind") or "").strip().lower()
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        if kind not in {"owner_task", "approval_request"}:
            raise HTTPException(status_code=400, detail="inbox_create_invalid_kind")
        if not title or not body:
            raise HTTPException(status_code=400, detail="inbox_create_title_body_required")

        severity = str(payload.get("severity") or "info").strip().lower() or "info"
        source = str(payload.get("source") or "owner-ui").strip().lower() or "owner-ui"
        channel_id = str(payload.get("channel_id") or "").strip()
        team_id = str(payload.get("team_id") or "").strip()
        source_item_id = str(payload.get("source_item_id") or "").strip()
        metadata = dict(payload.get("metadata") or {})

        try:
            if kind == "owner_task":
                if source_item_id:
                    result = inbox_service.escalate_item_to_owner_task(
                        source_item_id=source_item_id,
                        title=title,
                        body=body,
                        task_key=str(payload.get("task_key") or "").strip(),
                        source=source,
                        severity=severity,
                        metadata=metadata,
                    )
                else:
                    result = inbox_service.upsert_owner_task(
                        title=title,
                        body=body,
                        task_key=str(payload.get("task_key") or "").strip(),
                        source=source,
                        severity=severity,
                        channel_id=channel_id,
                        team_id=team_id,
                        trace_id=str(payload.get("trace_id") or "").strip(),
                        metadata=metadata,
                    )
            else:
                if source_item_id:
                    result = inbox_service.escalate_item_to_approval_request(
                        source_item_id=source_item_id,
                        title=title,
                        body=body,
                        request_key=str(payload.get("request_key") or "").strip(),
                        source=source,
                        severity=str(payload.get("severity") or "warning").strip().lower()
                        or "warning",
                        approval_scope=str(payload.get("approval_scope") or "owner").strip()
                        or "owner",
                        requested_action=str(payload.get("requested_action") or "").strip(),
                        metadata=metadata,
                    )
                else:
                    result = inbox_service.upsert_approval_request(
                        title=title,
                        body=body,
                        request_key=str(payload.get("request_key") or "").strip(),
                        source=source,
                        severity=str(payload.get("severity") or "warning").strip().lower()
                        or "warning",
                        channel_id=channel_id,
                        team_id=team_id,
                        trace_id=str(payload.get("trace_id") or "").strip(),
                        approval_scope=str(payload.get("approval_scope") or "owner").strip()
                        or "owner",
                        requested_action=str(payload.get("requested_action") or "").strip(),
                        metadata=metadata,
                    )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(
                status_code=404, detail=str(result.get("error") or "inbox_item_not_found")
            )

        return {
            "ok": True,
            "result": result,
        }

    # ---------------------------------------------------------------------
    # POST /api/inbox/create-vpn-alert (VPN Phase C — alerts bridge)
    # ---------------------------------------------------------------------

    # Допустимые severity-значения VPN watchdog'ов. Совпадают с inbox
    # severity, поэтому маппинг тривиален — единственная задача endpoint'а
    # лочить kind=vpn_alert и source=vpn-watchdog, чтобы каналы не
    # перемешивались.
    vpn_alert_severities = {"info", "warning", "error"}

    @router.post("/api/inbox/create-vpn-alert")
    async def inbox_create_vpn_alert(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Bridge VPN watchdog → Krab inbox.

        VPN watchdogs (`cert_guard`, `disk_guard`, `watchdog_vpn_panel`,
        `bruteforce_audit`) дополнительно к Telegram-нотификации через
        `alerts.env` шлют alert сюда. Endpoint thin: валидируем поля,
        нормализуем severity, кладём в inbox через `upsert_item` с
        kind=`vpn_alert` + dedupe по (source_script + title), чтобы
        повторные алерты от одного watchdog'а на одну тему не плодили
        дубликаты в UI.
        """
        ctx.assert_write_access(x_krab_web_key, token)

        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        source_script = str(payload.get("source_script") or "").strip()
        if not title or not body:
            raise HTTPException(status_code=400, detail="vpn_alert_title_body_required")
        if not source_script:
            raise HTTPException(status_code=400, detail="vpn_alert_source_script_required")

        severity = str(payload.get("severity") or "warning").strip().lower() or "warning"
        if severity not in vpn_alert_severities:
            raise HTTPException(status_code=400, detail="vpn_alert_invalid_severity")

        metadata = dict(payload.get("metadata") or {})
        metadata["source_script"] = source_script
        metadata["origin"] = "vpn-watchdog"

        # dedupe по watchdog+title — повторные сигналы upsert'ятся, не плодя items
        dedupe_key = f"vpn_alert::{source_script}::{title}"

        try:
            result = inbox_service.upsert_item(
                dedupe_key=dedupe_key,
                kind="vpn_alert",
                source="vpn-watchdog",
                title=title,
                body=body,
                severity=severity,
                metadata=metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {"ok": True, "result": result}

    return router
