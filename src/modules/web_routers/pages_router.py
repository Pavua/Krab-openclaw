# -*- coding: utf-8 -*-
"""
Pages router — Phase 2 Wave XX (Session 25, final architectural extraction).

Все HTML page routes из web_app.py: landing (/), dashboards (V4 + legacy +
primary aliases), prototypes, redirects /v4/* → /, и static CSS/JS assets,
которые отдают файлы из ``src/web/v4`` и ``src/web/prototypes``.

Контракт ответов сохранён 1:1 с inline definitions (FileResponse + cache
control headers, fallback HTML stub при missing файлах).

См. docs/CODE_SPLITS_PLAN.md.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from ...config import config
from ._context import RouterContext


def _no_store_headers() -> dict[str, str]:
    """Отключает браузерный кеш для owner-панели (см. web_app._no_store_headers)."""
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def _v4_path(name: str) -> Path:
    return config.BASE_DIR / "src" / "web" / "v4" / name


def _legacy_html_or_stub(filename: str, missing_html: str) -> HTMLResponse | FileResponse:
    page = config.BASE_DIR / "src" / "web" / filename
    if page.exists():
        return FileResponse(page, headers=_no_store_headers())
    return HTMLResponse(missing_html, headers=_no_store_headers())


def _v4_html_or_stub(filename: str, missing_html: str) -> HTMLResponse | FileResponse:
    page = _v4_path(filename)
    if page.exists():
        return FileResponse(page, headers=_no_store_headers())
    return HTMLResponse(missing_html, headers=_no_store_headers())


def build_pages_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter со всеми HTML page routes."""
    router = APIRouter(tags=["pages"])

    web_root = Path(__file__).resolve().parents[2] / "web"
    default_index_path = web_root / "index.html"
    default_nano_theme_path = web_root / "prototypes" / "nano" / "nano_theme.css"

    def _resolve_path(attr: str, default: Path) -> Path:
        """Достаём path из WebApp instance (если передан) или из ctx.deps,
        иначе — default. Lookup на каждый запрос — тесты могут патчить
        ``app._nano_theme_path`` / ``app._index_path`` после init."""
        webapp = ctx.deps.get("webapp") if ctx.deps else None
        if webapp is not None and hasattr(webapp, attr):
            value = getattr(webapp, attr)
            if isinstance(value, Path):
                return value
            if isinstance(value, str) and value:
                return Path(value)
        candidate = ctx.deps.get(attr) if ctx.deps else None
        if isinstance(candidate, Path):
            return candidate
        if isinstance(candidate, str) and candidate:
            return Path(candidate)
        return default

    # ── Landing + nano_theme.css ───────────────────────────────────────

    @router.get("/", response_class=HTMLResponse)
    async def index():
        index_path = _resolve_path("_index_path", default_index_path)
        if index_path.exists():
            return FileResponse(index_path, headers=_no_store_headers())
        from ..web_app_landing_page import LANDING_PAGE_HTML

        return HTMLResponse(LANDING_PAGE_HTML, headers=_no_store_headers())

    @router.get("/nano_theme.css")
    @router.get("/prototypes/nano/nano_theme.css")
    async def nano_theme_css():
        nano_theme_path = _resolve_path("_nano_theme_path", default_nano_theme_path)
        if nano_theme_path.exists():
            return FileResponse(
                nano_theme_path,
                media_type="text/css",
                headers=_no_store_headers(),
            )
        raise HTTPException(status_code=404, detail="nano_theme_css_not_found")

    # ── Stats + Legacy v3 dashboards ──────────────────────────────────

    @router.get("/stats", response_class=HTMLResponse)
    async def stats_dashboard():
        from ..web_app_stats_dashboard import STATS_DASHBOARD_HTML

        return HTMLResponse(STATS_DASHBOARD_HTML, headers=_no_store_headers())

    @router.get("/legacy/inbox", response_class=HTMLResponse)
    async def legacy_inbox_dashboard():
        return _legacy_html_or_stub("inbox_v2.html", "<h1>Legacy Inbox page not found</h1>")

    @router.get("/legacy/costs", response_class=HTMLResponse)
    async def legacy_costs_dashboard():
        return _legacy_html_or_stub("costs_v2.html", "<h1>Legacy Costs page not found</h1>")

    @router.get("/legacy/swarm", response_class=HTMLResponse)
    async def legacy_swarm_dashboard():
        return _legacy_html_or_stub("swarm_v2.html", "<h1>Legacy Swarm page not found</h1>")

    @router.get("/legacy/translator", response_class=HTMLResponse)
    async def legacy_translator_dashboard():
        return _legacy_html_or_stub(
            "translator_v2.html", "<h1>Legacy Translator page not found</h1>"
        )

    @router.get("/prototypes/{page}", response_class=HTMLResponse)
    async def prototype_page(page: str):
        safe_page = page.replace("..", "").replace("/", "")
        proto = config.BASE_DIR / "src" / "web" / "prototypes" / f"{safe_page}.html"
        if proto.exists():
            return FileResponse(proto, headers=_no_store_headers())
        proto_v1 = config.BASE_DIR / "src" / "web" / "prototypes" / f"{safe_page}_v1.html"
        if proto_v1.exists():
            return FileResponse(proto_v1, headers=_no_store_headers())
        return HTMLResponse(f"<h1>Prototype '{page}' not found</h1>", status_code=404)

    # ── V4 dashboard (Liquid Glass) ────────────────────────────────────

    @router.get("/v4", response_class=HTMLResponse)
    @router.get("/v4/", response_class=HTMLResponse)
    async def v4_index():
        return _v4_html_or_stub("index.html", "<h1>V4 not ready</h1>")

    @router.get("/v4/chat", response_class=HTMLResponse)
    async def v4_chat():
        return _v4_html_or_stub("chat.html", "<h1>V4 Chat not ready</h1>")

    @router.get("/v4/research", response_class=HTMLResponse)
    async def v4_research():
        return _v4_html_or_stub("research.html", "<h1>V4 Research not ready</h1>")

    # ── V4 primary routes (promoted to top-level) ─────────────────────

    @router.get("/costs", response_class=HTMLResponse)
    async def costs_dashboard():
        return _v4_html_or_stub("costs.html", "<h1>Costs dashboard not ready</h1>")

    @router.get("/inbox", response_class=HTMLResponse)
    async def inbox_dashboard():
        return _v4_html_or_stub("inbox.html", "<h1>Inbox dashboard not ready</h1>")

    @router.get("/swarm", response_class=HTMLResponse)
    async def swarm_dashboard():
        return _v4_html_or_stub("swarm.html", "<h1>Swarm dashboard not ready</h1>")

    @router.get("/translator", response_class=HTMLResponse)
    async def translator_dashboard():
        return _v4_html_or_stub("translator.html", "<h1>Translator dashboard not ready</h1>")

    @router.get("/ops", response_class=HTMLResponse)
    async def ops_dashboard():
        return _v4_html_or_stub("ops.html", "<h1>Ops dashboard not ready</h1>")

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_dashboard():
        return _v4_html_or_stub("settings.html", "<h1>Settings dashboard not ready</h1>")

    @router.get("/commands", response_class=HTMLResponse)
    async def commands_dashboard():
        return _v4_html_or_stub("commands.html", "<h1>Commands dashboard not ready</h1>")

    # ── /v4/* → 301 redirects to primary routes ───────────────────────

    @router.get("/v4/costs", response_class=HTMLResponse)
    async def v4_costs_redirect():
        return RedirectResponse(url="/costs", status_code=301)

    @router.get("/v4/inbox", response_class=HTMLResponse)
    async def v4_inbox_redirect():
        return RedirectResponse(url="/inbox", status_code=301)

    @router.get("/v4/swarm", response_class=HTMLResponse)
    async def v4_swarm_redirect():
        return RedirectResponse(url="/swarm", status_code=301)

    @router.get("/v4/translator", response_class=HTMLResponse)
    async def v4_translator_redirect():
        return RedirectResponse(url="/translator", status_code=301)

    @router.get("/v4/ops", response_class=HTMLResponse)
    async def v4_ops_redirect():
        return RedirectResponse(url="/ops", status_code=301)

    @router.get("/v4/settings", response_class=HTMLResponse)
    async def v4_settings_redirect():
        return RedirectResponse(url="/settings", status_code=301)

    @router.get("/v4/commands", response_class=HTMLResponse)
    async def v4_commands_redirect():
        return RedirectResponse(url="/commands", status_code=301)

    # ── Legacy v3 stubs (no v3 page existed) ──────────────────────────

    @router.get("/legacy/ops", response_class=HTMLResponse)
    async def legacy_ops_dashboard():
        return HTMLResponse(
            "<h1>Legacy Ops</h1><p>No v3 ops page — <a href='/ops'>go to V4 Ops</a></p>",
            headers=_no_store_headers(),
        )

    @router.get("/legacy/settings", response_class=HTMLResponse)
    async def legacy_settings_dashboard():
        return HTMLResponse(
            "<h1>Legacy Settings</h1><p>No v3 settings page — <a href='/settings'>go to V4 Settings</a></p>",
            headers=_no_store_headers(),
        )

    @router.get("/legacy/commands", response_class=HTMLResponse)
    async def legacy_commands_dashboard():
        return HTMLResponse(
            "<h1>Legacy Commands</h1><p>No v3 commands page — <a href='/commands'>go to V4 Commands</a></p>",
            headers=_no_store_headers(),
        )

    # ── V4 static assets (CSS + JS) ───────────────────────────────────

    @router.get("/v4/liquid-glass.css")
    async def v4_css():
        css = _v4_path("liquid-glass.css")
        if css.exists():
            return FileResponse(css, media_type="text/css", headers=_no_store_headers())
        return HTMLResponse("/* not found */", media_type="text/css")

    @router.get("/v4/theme-toggle.js")
    async def v4_theme_toggle():
        js = _v4_path("theme-toggle.js")
        if js.exists():
            return FileResponse(
                js, media_type="application/javascript", headers=_no_store_headers()
            )
        return HTMLResponse("// not found", media_type="application/javascript")

    return router
