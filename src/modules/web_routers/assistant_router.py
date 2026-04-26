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
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from ._context import RouterContext


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

    return router
