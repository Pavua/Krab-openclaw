# -*- coding: utf-8 -*-
"""
Admin router — Phase 2 Wave W extraction (Session 25).

Объединяет administrative endpoints:
- Provisioning (draft lifecycle для catalog entities):
  - GET  /api/provisioning/templates
  - GET  /api/provisioning/drafts
  - POST /api/provisioning/drafts
  - GET  /api/provisioning/preview/{draft_id}
  - POST /api/provisioning/apply/{draft_id}

- Userbot ACL (runtime ACL snapshot + update):
  - GET  /api/userbot/acl/status
  - POST /api/userbot/acl/update

Контракт ответов сохранён 1:1 с inline definitions в web_app.py.

Helpers/services инжектируются через ``ctx.deps`` в ``_make_router_context``:
- ``provisioning_service`` — singleton (как и раньше).
- ``black_box`` — observability sink (опционально).
- ``idempotency_get`` / ``idempotency_set`` — write endpoints используют
  shared cache из WebApp instance (replay через X-Idempotency-Key).
- ACL helpers (``acl_load_state_helper``, ``acl_owner_label_helper``,
  ``acl_owner_subjects_helper``, ``acl_update_subject_helper``,
  ``acl_partial_commands``, ``acl_file_path``) — инжектируются bound на
  ``src.modules.web_app.<symbol>``, чтобы существующие тесты, патчащие
  ``src.modules.web_app.load_acl_runtime_state`` и аналогичные, продолжали
  работать без модификаций (dual-patch стратегия).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request

from ._context import RouterContext


def build_admin_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с provisioning + userbot ACL endpoints."""
    router = APIRouter(tags=["admin"])

    # ── Provisioning ────────────────────────────────────────────────────────

    @router.get("/api/provisioning/templates")
    async def provisioning_templates(entity: str = Query(default="agent")) -> dict:
        """Возвращает шаблоны для provisioning UI/API."""
        provisioning = ctx.get_dep("provisioning_service")
        if not provisioning:
            raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
        return {"entity": entity, "templates": provisioning.list_templates(entity)}

    @router.get("/api/provisioning/drafts")
    async def provisioning_drafts(
        status: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=200),
    ) -> dict:
        """Список provisioning draft'ов."""
        provisioning = ctx.get_dep("provisioning_service")
        if not provisioning:
            raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
        return {"drafts": provisioning.list_drafts(limit=limit, status=status)}

    @router.post("/api/provisioning/drafts")
    async def provisioning_create_draft(
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Создает provisioning draft (write endpoint)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        idem_get = ctx.get_dep("idempotency_get")
        idem_set = ctx.get_dep("idempotency_set")
        idem_key = (x_idempotency_key or "").strip()
        if idem_get is not None:
            cached = idem_get("provisioning_create_draft", idem_key)
            if cached:
                return cached
        provisioning = ctx.get_dep("provisioning_service")
        if not provisioning:
            raise HTTPException(status_code=503, detail="provisioning_service_not_configured")

        try:
            draft = provisioning.create_draft(
                entity_type=payload.get("entity_type", "agent"),
                name=payload.get("name", ""),
                role=payload.get("role", ""),
                description=payload.get("description", ""),
                requested_by=payload.get("requested_by", "web_api"),
                settings=payload.get("settings", {}),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        black_box = ctx.get_dep("black_box")
        if black_box and hasattr(black_box, "log_event"):
            black_box.log_event(
                "web_provisioning_draft_create",
                f"entity={payload.get('entity_type', 'agent')} name={payload.get('name', '')}",
            )
        response_payload = {"ok": True, "draft": draft}
        if idem_set is not None:
            idem_set("provisioning_create_draft", idem_key, response_payload)
        return response_payload

    @router.get("/api/provisioning/preview/{draft_id}")
    async def provisioning_preview(draft_id: str) -> dict:
        """Показывает diff для draft перед apply."""
        provisioning = ctx.get_dep("provisioning_service")
        if not provisioning:
            raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
        try:
            preview = provisioning.preview_diff(draft_id)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "preview": preview}

    @router.post("/api/provisioning/apply/{draft_id}")
    async def provisioning_apply(
        draft_id: str,
        confirm: bool = Query(default=False),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Применяет draft в catalog (write endpoint)."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        idem_get = ctx.get_dep("idempotency_get")
        idem_set = ctx.get_dep("idempotency_set")
        idem_key = (x_idempotency_key or "").strip()
        idem_namespace_key = f"{draft_id}:{idem_key}"
        if idem_get is not None:
            cached = idem_get("provisioning_apply", idem_namespace_key)
            if cached:
                return cached
        provisioning = ctx.get_dep("provisioning_service")
        if not provisioning:
            raise HTTPException(status_code=503, detail="provisioning_service_not_configured")
        try:
            result = provisioning.apply_draft(draft_id, confirmed=confirm)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        black_box = ctx.get_dep("black_box")
        if black_box and hasattr(black_box, "log_event"):
            black_box.log_event(
                "web_provisioning_apply",
                f"draft_id={draft_id} confirmed={confirm}",
            )
        response_payload = {"ok": True, "result": result}
        if idem_set is not None:
            idem_set("provisioning_apply", idem_namespace_key, response_payload)
        return response_payload

    # ── Userbot ACL ─────────────────────────────────────────────────────────

    @router.get("/api/userbot/acl/status")
    async def userbot_acl_status() -> dict:
        """Read-only runtime ACL userbot."""
        load_state = ctx.get_dep("acl_load_state_helper")
        owner_label = ctx.get_dep("acl_owner_label_helper")
        owner_subjects = ctx.get_dep("acl_owner_subjects_helper")
        partial_commands = ctx.get_dep("acl_partial_commands") or set()
        acl_file_path = ctx.get_dep("acl_file_path") or ""
        if not (load_state and owner_label and owner_subjects):
            raise HTTPException(status_code=503, detail="acl_helpers_not_configured")
        return {
            "ok": True,
            "acl": {
                "path": str(acl_file_path),
                "owner_username": owner_label(),
                "owner_subjects": owner_subjects(),
                "state": load_state(),
                "partial_commands": sorted(partial_commands),
            },
        }

    @router.post("/api/userbot/acl/update")
    async def userbot_acl_update(
        request: Request,
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """Обновляет runtime ACL userbot через owner web-key."""
        ctx.assert_write_access_fn(x_krab_web_key, token)
        update_subject = ctx.get_dep("acl_update_subject_helper")
        partial_commands = ctx.get_dep("acl_partial_commands") or set()
        if not update_subject:
            raise HTTPException(status_code=503, detail="acl_helpers_not_configured")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="acl_update_body_required")
        action = str(body.get("action") or "").strip().lower()
        level = str(body.get("level") or "").strip().lower()
        subject = str(body.get("subject") or "").strip()
        if action not in {"grant", "revoke"}:
            raise HTTPException(status_code=400, detail="acl_update_invalid_action")
        try:
            result = update_subject(level, subject, add=(action == "grant"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "acl": {
                "action": action,
                "level": result["level"],
                "subject": result["subject"],
                "changed": bool(result["changed"]),
                "path": str(result["path"]),
                "state": result["state"],
                "partial_commands": sorted(partial_commands),
            },
        }

    return router
