# -*- coding: utf-8 -*-
"""
Browser router — Phase 2 Wave U extraction (Session 25).

Endpoints (8):
- GET  /api/browser/status        — статус CDP-подключения + tab_count.
- GET  /api/browser/tabs          — список вкладок browser_bridge.
- POST /api/browser/navigate      — навигация на URL.
- POST /api/browser/screenshot    — скриншот активной вкладки (base64).
- POST /api/browser/read          — читаемый текст текущей страницы.
- POST /api/browser/js            — exec JS кода в активной вкладке.
- GET  /api/chrome/dedicated/status — статус isolated dedicated Chrome.
- POST /api/chrome/dedicated/launch — manual launch dedicated Chrome.

Контракт ответов сохранён 1:1 с inline definitions из web_app.py.

Замечание по тестированию: existing tests
(``tests/unit/test_web_browser_macos_api.py``) патчат
``sys.modules['src.integrations.browser_bridge'].browser_bridge`` ДО создания
WebApp. Чтобы этот патч работал, router резолвит ``browser_bridge`` через
module-attribute lookup на каждом запросе (а не захватывает ссылку в closure
на этапе ``include_router``).
"""

from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ._context import RouterContext

_BROWSER_BRIDGE_TIMEOUT_SEC = 8.0


def _bb():
    """Возвращает текущий browser_bridge через module-attribute lookup.

    Lookup на каждом вызове намеренно — позволяет тестам патчить
    ``sys.modules['src.integrations.browser_bridge'].browser_bridge``.
    """
    from ...integrations import browser_bridge as _bb_mod

    return _bb_mod.browser_bridge


def build_browser_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с browser/chrome endpoints."""
    router = APIRouter(tags=["browser"])

    # ── Browser Bridge API ──────────────────────────────────────────────
    @router.get("/api/browser/status")
    async def browser_status() -> dict:
        try:
            attached = await asyncio.wait_for(
                _bb().is_attached(), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC
            )
            tabs = (
                await asyncio.wait_for(_bb().list_tabs(), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC)
                if attached
                else []
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": "browser_timeout",
                "detail": str(exc),
                "attached": False,
                "tab_count": 0,
                "active_url": None,
            }
        active_url = tabs[-1]["url"] if tabs else None
        return {
            "ok": True,
            "attached": attached,
            "tab_count": len(tabs),
            "active_url": active_url,
        }

    @router.get("/api/browser/tabs")
    async def browser_tabs():
        try:
            tabs = await asyncio.wait_for(_bb().list_tabs(), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC)
        except Exception as exc:
            return {"ok": False, "error": "browser_timeout", "detail": str(exc), "tabs": []}
        return tabs

    @router.post("/api/browser/navigate")
    async def browser_navigate(body: dict = Body(...)) -> dict:
        url = str(body.get("url") or "").strip()
        if not url:
            raise HTTPException(status_code=400, detail="url required")
        try:
            current_url = await asyncio.wait_for(
                _bb().navigate(url), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC
            )
        except Exception as exc:
            return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
        return {"ok": True, "current_url": current_url}

    @router.post("/api/browser/screenshot")
    async def browser_screenshot() -> dict:
        try:
            data = await asyncio.wait_for(
                _bb().screenshot_base64(), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC
            )
        except Exception as exc:
            return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
        if data is None:
            return {"ok": False, "error": "screenshot_failed"}
        return {"ok": True, "data": data}

    @router.post("/api/browser/read")
    async def browser_read() -> dict:
        try:
            text = await asyncio.wait_for(
                _bb().get_page_text(), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC
            )
        except Exception as exc:
            return {"ok": False, "error": "browser_timeout", "detail": str(exc), "text": ""}
        return {"ok": True, "text": text}

    @router.post("/api/browser/js")
    async def browser_js(body: dict = Body(...)) -> dict:
        code = str(body.get("code") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="code required")
        try:
            result = await asyncio.wait_for(
                _bb().execute_js(code), timeout=_BROWSER_BRIDGE_TIMEOUT_SEC
            )
        except Exception as exc:
            return {"ok": False, "error": "browser_timeout", "detail": str(exc)}
        return {"ok": True, "result": result}

    # ── Dedicated Chrome ────────────────────────────────────────────────
    @router.get("/api/chrome/dedicated/status")
    async def chrome_dedicated_status() -> dict:
        """Статус dedicated Chrome (isolated profile на /tmp/krab-chrome)."""
        from ...integrations.dedicated_chrome import (
            DEFAULT_CDP_PORT,
            find_chrome_binary,
            is_dedicated_chrome_running,
        )

        port = int(os.environ.get("DEDICATED_CHROME_PORT") or DEFAULT_CDP_PORT)
        enabled = os.environ.get("DEDICATED_CHROME_ENABLED", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        return {
            "ok": True,
            "enabled": enabled,
            "running": is_dedicated_chrome_running(port),
            "port": port,
            "binary": find_chrome_binary(),
            "profile_dir": os.environ.get("DEDICATED_CHROME_PROFILE_DIR") or "/tmp/krab-chrome",
        }

    @router.post("/api/chrome/dedicated/launch")
    async def chrome_dedicated_launch(
        token: str = Query(default=""),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
    ) -> dict:
        """Ручной запуск dedicated Chrome (идемпотентно)."""
        ctx.assert_write_access(x_krab_web_key, token)
        from ...integrations.dedicated_chrome import launch_dedicated_chrome

        ok, status = await asyncio.to_thread(launch_dedicated_chrome)
        return {"ok": ok, "status": status}

    return router
