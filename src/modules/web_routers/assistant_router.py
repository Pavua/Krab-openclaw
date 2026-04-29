# -*- coding: utf-8 -*-
"""
Assistant router — Phase 2 Wave V extraction (Session 25).

Endpoints:
- GET  /api/assistant/capabilities — snapshot возможностей web-native ассистента.
- POST /api/assistant/attachment   — загрузка вложения для web-assistant
                                     (text/PDF/DOCX/image/video/archive).

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Замечания:
- ``/api/assistant/query`` (POST, ~300 LOC) намеренно НЕ извлечён в Wave V —
  endpoint завязан на множество self._* helper-методов (rate limit, idempotency,
  router pipeline) и требует отдельной волны.
- ``/api/assistant/stream`` (GET, SSE) тоже отложён — streaming endpoint
  заслуживает выделенного refactor pass.
- Helpers (``_assistant_capabilities_snapshot``, ``_web_attachment_max_bytes``,
  ``_sanitize_attachment_name``, ``_build_attachment_prompt``) инжектируются
  через ``deps`` в ``_make_router_context`` (Phase 2 pattern).
"""

from __future__ import annotations

import hashlib
import mimetypes
import shlex
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Body, File, Header, HTTPException, Query, Request, UploadFile

from src.core.model_aliases import (
    normalize_model_alias,
    parse_model_set_request,
    render_model_presets_text,
)

from ._context import RouterContext


def _normalize_force_mode(force_mode: str) -> str:
    """Нормализует внутренние force_* режимы в UI-вид: auto/local/cloud."""
    normalized = str(force_mode or "").strip().lower()
    if normalized in {"force_local", "local"}:
        return "local"
    if normalized in {"force_cloud", "cloud"}:
        return "cloud"
    return "auto"


def _is_model_status_question(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    patterns = [
        "на какой модел",
        "какой моделью",
        "какая модель",
        "на чем работаешь",
        "через какую модель",
        "what model",
        "which model",
    ]
    return any(p in low for p in patterns)


def _build_model_status_from_route(route: dict) -> str:
    channel = str(route.get("channel", "unknown"))
    model = str(route.get("model", "unknown"))
    provider = str(route.get("provider", "unknown"))
    tier = str(route.get("active_tier", "-"))
    return (
        "🧭 Фактический runtime-маршрут:\n"
        f"- Канал: `{channel}`\n"
        f"- Модель: `{model}`\n"
        f"- Провайдер: `{provider}`\n"
        f"- Cloud tier: `{tier}`"
    )


def build_assistant_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с assistant capabilities + attachment."""
    router = APIRouter(tags=["assistant"])

    @router.get("/api/assistant/capabilities")
    async def assistant_capabilities() -> dict:
        """Возвращает возможности web-native assistant режима."""
        helper = ctx.get_dep("assistant_capabilities_snapshot_helper")
        if helper is None:
            raise HTTPException(
                status_code=503,
                detail="assistant_capabilities_helper_not_configured",
            )
        return helper()

    @router.post("/api/assistant/attachment")
    async def assistant_attachment_upload(
        file: UploadFile = File(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict:
        """
        Загружает вложение для web-assistant и возвращает prompt-snippet.
        Поддерживает текст/PDF/DOCX (извлечение текста best effort),
        а также изображения/видео/архивы (метаданные + локальный путь).
        """
        ctx.assert_write_access(x_krab_web_key, token)

        max_bytes_fn = ctx.get_dep("assistant_attachment_max_bytes_helper")
        sanitize_name_fn = ctx.get_dep("assistant_attachment_sanitize_name_helper")
        build_prompt_fn = ctx.get_dep("assistant_attachment_build_prompt_helper")
        if not (max_bytes_fn and sanitize_name_fn and build_prompt_fn):
            raise HTTPException(
                status_code=503,
                detail="assistant_attachment_helpers_not_configured",
            )

        black_box = ctx.get_dep("black_box")

        if not file:
            raise HTTPException(status_code=400, detail="assistant_attachment_file_required")
        original_name = str(file.filename or "").strip()
        if not original_name:
            raise HTTPException(status_code=400, detail="assistant_attachment_filename_required")

        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="assistant_attachment_empty_file")

        max_bytes = max_bytes_fn()
        if len(raw) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"assistant_attachment_too_large: max={max_bytes} bytes",
            )

        safe_name = sanitize_name_fn(original_name)
        guessed_type = mimetypes.guess_type(safe_name)[0] or ""
        content_type = str(file.content_type or guessed_type or "application/octet-stream")

        uploads_dir = Path("artifacts/web_uploads")
        uploads_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        short_hash = hashlib.sha256(raw).hexdigest()[:10]
        stored_name = f"{ts}_{short_hash}_{safe_name}"
        stored_path = uploads_dir / stored_name
        stored_path.write_bytes(raw)

        attachment = build_prompt_fn(
            file_name=safe_name,
            content_type=content_type,
            raw_bytes=raw,
            stored_path=stored_path,
        )

        if black_box and hasattr(black_box, "log_event"):
            black_box.log_event(
                "web_assistant_attachment",
                f"name={safe_name} type={content_type} size={len(raw)} kind={attachment.get('kind')}",
            )

        return {"ok": True, "attachment": attachment}

    # ------------------------------------------------------------------
    # Phase 2 Part 2D (Session 27): /api/assistant/query — main pipeline
    # ------------------------------------------------------------------

    @router.post("/api/assistant/query")
    async def assistant_query(
        request: Request,
        payload: dict = Body(...),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        x_krab_client: str = Header(default="", alias="X-Krab-Client"),
        x_idempotency_key: str = Header(default="", alias="X-Idempotency-Key"),
        token: str = Query(default=""),
    ):
        """Web-first AI query — full pipeline (rate limit + idempotency + router)."""
        ctx.assert_write_access(x_krab_web_key, token)

        idempotency_get = ctx.get_dep("idempotency_get")
        idempotency_set = ctx.get_dep("idempotency_set")
        rate_limit_helper = ctx.get_dep("assistant_rate_limit_helper")
        router_obj = ctx.get_dep("router")
        black_box = ctx.get_dep("black_box")

        if not (idempotency_get and idempotency_set and rate_limit_helper):
            raise HTTPException(status_code=503, detail="assistant_query_helpers_not_configured")

        client_ip = request.client.host if request.client else "unknown"
        client_key = (x_krab_client or "").strip() or client_ip
        idem_key = (x_idempotency_key or "").strip()
        cached = idempotency_get("assistant_query", idem_key)
        if cached:
            return cached
        rate_limit_helper(client_key)

        if not router_obj:
            raise HTTPException(status_code=503, detail="router_not_configured")

        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt_required")

        task_type = str(payload.get("task_type", "chat")).strip().lower() or "chat"
        use_rag = bool(payload.get("use_rag", False))
        preferred_model = payload.get("preferred_model")
        preferred_model_str = str(preferred_model).strip() if preferred_model else None
        confirm_expensive = bool(payload.get("confirm_expensive", False))
        requested_force_mode_raw = str(payload.get("force_mode", "")).strip().lower()
        requested_force_mode = (
            requested_force_mode_raw
            if requested_force_mode_raw in {"auto", "local", "cloud"}
            else ""
        )

        # Web UX-хелпер: поддержка `.model ...` и `!model ...` команд из UI.
        command_prompt = prompt
        if command_prompt.startswith(".model"):
            command_prompt = f"!{command_prompt[1:]}"

        if command_prompt.startswith("!model"):
            try:
                tokens = shlex.split(command_prompt[1:])
            except Exception:
                tokens = command_prompt[1:].split()

            if not tokens or tokens[0].lower() != "model":
                raise HTTPException(status_code=400, detail="assistant_model_command_invalid")

            subcommand = tokens[1].strip().lower() if len(tokens) >= 2 else ""
            if subcommand in {"presets", "catalog", "quick"}:
                response_payload = {
                    "ok": True,
                    "mode": "web_native",
                    "task_type": task_type,
                    "profile": "chat",
                    "command_mode": True,
                    "last_route": router_obj.get_last_route()
                    if hasattr(router_obj, "get_last_route")
                    else {},
                    "reply": render_model_presets_text(),
                }
                idempotency_set("assistant_query", idem_key, response_payload)
                return response_payload

            if subcommand in {"local", "cloud", "auto"} and hasattr(router_obj, "set_force_mode"):
                result = router_obj.set_force_mode(subcommand)
                response_payload = {
                    "ok": True,
                    "mode": "web_native",
                    "task_type": task_type,
                    "profile": "chat",
                    "command_mode": True,
                    "last_route": router_obj.get_last_route()
                    if hasattr(router_obj, "get_last_route")
                    else {},
                    "reply": f"✅ Режим обновлен: {result}",
                }
                idempotency_set("assistant_query", idem_key, response_payload)
                return response_payload

            if subcommand == "set":
                if len(tokens) == 3:
                    tokens = [tokens[0], tokens[1], "chat", tokens[2]]
                parsed = parse_model_set_request(tokens, list(router_obj.models.keys()))
                if not parsed.get("ok"):
                    response_payload = {
                        "ok": True,
                        "mode": "web_native",
                        "task_type": task_type,
                        "profile": "chat",
                        "command_mode": True,
                        "last_route": router_obj.get_last_route()
                        if hasattr(router_obj, "get_last_route")
                        else {},
                        "reply": str(parsed.get("error") or "❌ Некорректная команда"),
                    }
                    idempotency_set("assistant_query", idem_key, response_payload)
                    return response_payload

                slot = str(parsed["slot"])
                model_raw = str(parsed["model_name"])
                model_resolved, alias_note = normalize_model_alias(model_raw)
                old_value = str(router_obj.models.get(slot, "—"))
                router_obj.models[slot] = model_resolved

                reply_lines = []
                if parsed.get("warning"):
                    reply_lines.append(str(parsed["warning"]))
                if alias_note:
                    reply_lines.append(alias_note)
                reply_lines.append(f"✅ Slot `{slot}` обновлен: `{old_value}` → `{model_resolved}`")
                reply_lines.append("Подсказка: `!model` или `!model preflight chat Тест`")

                response_payload = {
                    "ok": True,
                    "mode": "web_native",
                    "task_type": task_type,
                    "profile": "chat",
                    "command_mode": True,
                    "last_route": router_obj.get_last_route()
                    if hasattr(router_obj, "get_last_route")
                    else {},
                    "reply": "\n".join(reply_lines),
                }
                idempotency_set("assistant_query", idem_key, response_payload)
                return response_payload

        try:
            if requested_force_mode and hasattr(router_obj, "set_force_mode"):
                router_obj.set_force_mode(requested_force_mode)
            effective_force_mode = _normalize_force_mode(getattr(router_obj, "force_mode", "auto"))

            reply = await router_obj.route_query(
                prompt=prompt,
                task_type=task_type,
                context=[],
                chat_type="private",
                is_owner=True,
                use_rag=use_rag,
                preferred_model=preferred_model_str,
                confirm_expensive=confirm_expensive,
            )

            # Local-first аварийная деградация при leaked cloud key.
            leaked_key_marker = "reported as leaked"
            if (
                isinstance(reply, str)
                and leaked_key_marker in reply.lower()
                and effective_force_mode != "cloud"
                and hasattr(router_obj, "check_local_health")
            ):
                local_ok = bool(await router_obj.check_local_health(force=True))
                if local_ok:
                    previous_mode = str(getattr(router_obj, "force_mode", "auto"))
                    try:
                        router_obj.force_mode = "force_local"
                        local_reply = await router_obj.route_query(
                            prompt=prompt,
                            task_type=task_type,
                            context=[],
                            chat_type="private",
                            is_owner=True,
                            use_rag=use_rag,
                            preferred_model=None,
                            confirm_expensive=confirm_expensive,
                        )
                        if isinstance(local_reply, str) and local_reply.strip():
                            reply = (
                                "⚠️ Cloud API key отклонён (`reported as leaked`). "
                                "Переключился на local-first ответ.\n\n"
                                f"{local_reply}"
                            )
                    finally:
                        router_obj.force_mode = previous_mode
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"assistant_query_failed: {exc}") from exc

        profile = (
            router_obj.classify_task_profile(prompt, task_type)
            if hasattr(router_obj, "classify_task_profile")
            else task_type
        )
        recommendation = (
            router_obj.get_profile_recommendation(profile)
            if hasattr(router_obj, "get_profile_recommendation")
            else {"profile": profile}
        )
        if hasattr(router_obj, "get_task_preflight"):
            try:
                preflight = router_obj.get_task_preflight(
                    prompt=prompt,
                    task_type=task_type,
                    preferred_model=preferred_model_str,
                    confirm_expensive=confirm_expensive,
                )
            except Exception:
                preflight = {}
            if isinstance(preflight, dict):
                execution = (
                    preflight.get("execution")
                    if isinstance(preflight.get("execution"), dict)
                    else {}
                )
                recommended_model = str(
                    execution.get("model")
                    or recommendation.get("model")
                    or recommendation.get("recommended_model")
                    or ""
                ).strip()
                recommended_channel = str(
                    execution.get("channel") or recommendation.get("channel") or ""
                ).strip()
                reason_lines = (
                    preflight.get("reasons") if isinstance(preflight.get("reasons"), list) else []
                )
                recommendation = {
                    **(recommendation if isinstance(recommendation, dict) else {}),
                    "profile": str(preflight.get("profile") or profile),
                    "model": recommended_model,
                    "recommended_model": recommended_model,
                    "channel": recommended_channel,
                    "reasoning": "; ".join(str(item) for item in reason_lines if str(item).strip())
                    or str((recommendation or {}).get("reasoning") or ""),
                    "local_available": bool(
                        preflight.get(
                            "local_available",
                            (recommendation or {}).get("local_available", False),
                        )
                    ),
                    "force_mode": str(
                        execution.get("force_mode")
                        or (recommendation or {}).get("force_mode")
                        or "auto"
                    ),
                }
        last_route = router_obj.get_last_route() if hasattr(router_obj, "get_last_route") else {}
        if black_box and hasattr(black_box, "log_event"):
            black_box.log_event(
                "web_assistant_query",
                f"task_type={task_type} profile={profile} prompt_len={len(prompt)} client={client_key}",
            )
        response_payload = {
            "ok": True,
            "mode": "web_native",
            "task_type": task_type,
            "profile": profile,
            "effective_force_mode": _normalize_force_mode(
                getattr(router_obj, "force_mode", "auto")
            ),
            "recommendation": recommendation,
            "last_route": last_route,
            "reply": reply,
        }
        if (
            _is_model_status_question(prompt)
            and isinstance(last_route, dict)
            and last_route.get("model")
        ):
            response_payload["reply"] = _build_model_status_from_route(last_route)
        idempotency_set("assistant_query", idem_key, response_payload)
        return response_payload

    # ------------------------------------------------------------------
    # Phase 2 Part 2C (Session 27): /api/assistant/stream — SSE streaming
    # ------------------------------------------------------------------

    @router.get("/api/assistant/stream")
    async def assistant_stream(
        prompt: str = Query(default=""),
        token: str = Query(default=""),
        task_type: str = Query(default="chat"),
    ):
        """SSE streaming для AI Chat dashboard.

        Auth: dev-mode — SSE chat доступен без ключа (write-endpoints
        защищены отдельно через X-Krab-Web-Key). Контракт сохранён 1:1
        с inline-определением web_app.py.
        """
        from fastapi.responses import StreamingResponse as _StreamingResponse

        if not prompt.strip():
            return {"ok": False, "error": "empty prompt"}

        async def event_generator():
            import json as _json

            yield f"event: status\ndata: {_json.dumps({'phase': 'routing'})}\n\n"

            try:
                from ...openclaw_client import openclaw_client

                yield f"event: status\ndata: {_json.dumps({'phase': 'processing'})}\n\n"

                chunks = []
                async for chunk in openclaw_client.send_message_stream(
                    message=prompt,
                    chat_id=f"web_chat_{id(prompt) % 10000}",
                    system_prompt="Ты — AI ассистент Krab. Отвечай полезно и по делу.",
                    force_cloud=True,
                ):
                    chunks.append(chunk)

                reply = "".join(chunks).strip()

                if hasattr(openclaw_client, "_active_tool_calls"):
                    for i, tc in enumerate(openclaw_client._active_tool_calls):
                        yield (
                            f"event: tool_done\ndata: "
                            f"{_json.dumps({'name': tc.get('name', '?'), 'index': i})}\n\n"
                        )

                route = {}
                if hasattr(openclaw_client, "get_last_runtime_route"):
                    route = openclaw_client.get_last_runtime_route() or {}

                yield (
                    f"event: route\ndata: "
                    f"{_json.dumps({'model': route.get('model', '?'), 'provider': route.get('provider', '?')})}\n\n"
                )
                yield f"event: message\ndata: {_json.dumps({'reply': reply})}\n\n"

            except Exception as exc:  # noqa: BLE001
                yield f"event: error\ndata: {_json.dumps({'error': str(exc)})}\n\n"

            yield "event: done\ndata: {}\n\n"

        return _StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
