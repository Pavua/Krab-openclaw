# -*- coding: utf-8 -*-
"""
MCP Gateway router — Wave 236 (Session 44+).

Экспонирует tool registry Krab как standalone MCP server endpoint —
Cherry Studio, IDE и любой другой MCP-клиент может видеть и вызывать
инструменты Krab напрямую через HTTP/SSE без proxy.

Endpoints:
- GET    /api/mcp/server/info       — server info: name/version/protocolVersion
- POST   /api/mcp/tools/list        — список tool definitions (MCP spec format)
- POST   /api/mcp/tools/call        — body {name, arguments} → exec
- GET    /api/mcp/sse               — Server-Sent Events stream для streaming
- GET    /admin/mcp                 — HTML page с tool list + test runner

Совместимость с MCP spec (2024-11-05):
- Schema объекты эквивалентны JSON-RPC ответам: {tools: [{name, description, inputSchema}]}
- inputSchema — стандартный JSON Schema объект
- tools/call возвращает {content: [{type: "text", text: ...}], isError: bool}

Auth (write endpoints — tools/call):
- bearer ``Authorization: Bearer <WEB_API_KEY>`` (Cherry Studio style)
- legacy ``X-Krab-Web-Key`` / ``?token=`` (consistency с другими routers)

Tool source:
- ``src.mcp_client.mcp_manager.get_tool_manifest()`` — OpenAI Tool Definition
  format (functions/parameters). Конвертируем в MCP-native shape:
  ``{name, description, inputSchema}``.

Tests могут инжектировать заглушку через ``ctx.deps['mcp_manager']``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from ._context import RouterContext

# MCP protocol version, см. https://spec.modelcontextprotocol.io/
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "Krab"
SERVER_VERSION = "1.0.0"


def _resolve_mcp_manager(ctx: RouterContext):  # noqa: ANN202
    """Resolve MCP manager: ctx.deps override → module singleton."""
    mgr = ctx.get_dep("mcp_manager")
    if mgr is not None:
        return mgr
    try:
        from ...mcp_client import mcp_manager as _singleton

        return _singleton
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"mcp_manager_unavailable: {exc}",
        ) from exc


def _openai_to_mcp_tool(item: dict[str, Any]) -> dict[str, Any] | None:
    """Конвертирует OpenAI-style tool definition в MCP-native shape.

    Input shape (OpenAI):
        {"type": "function", "function": {"name", "description", "parameters"}}

    Output shape (MCP spec):
        {"name", "description", "inputSchema"}

    Returns None если item невалиден.
    """
    if not isinstance(item, dict):
        return None
    fn = item.get("function") if isinstance(item.get("function"), dict) else None
    if fn is None:
        # Возможно, элемент уже в MCP-native формате — пропустим как есть.
        if "name" in item and ("inputSchema" in item or "input_schema" in item):
            return {
                "name": str(item["name"]),
                "description": str(item.get("description", "")),
                "inputSchema": item.get("inputSchema") or item.get("input_schema") or {},
            }
        return None
    name = fn.get("name")
    if not name:
        return None
    return {
        "name": str(name),
        "description": str(fn.get("description", "")),
        "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


async def _list_mcp_tools(ctx: RouterContext) -> list[dict[str, Any]]:
    """Получает manifest и возвращает MCP-native список инструментов."""
    mgr = _resolve_mcp_manager(ctx)
    try:
        manifest = await mgr.get_tool_manifest()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"tool_manifest_failed: {exc}",
        ) from exc
    out: list[dict[str, Any]] = []
    for item in manifest or []:
        converted = _openai_to_mcp_tool(item)
        if converted is not None:
            out.append(converted)
    return out


def _check_bearer_or_legacy(
    ctx: RouterContext,
    *,
    authorization: str,
    x_krab_web_key: str,
    token: str,
) -> None:
    """Auth check: Bearer header (Cherry Studio) OR legacy X-Krab-Web-Key/token.

    Если WEB_API_KEY не установлен — открытый доступ.
    """
    expected = (ctx.web_api_key_fn() or "").strip() if ctx.web_api_key_fn else ""
    if not expected:
        # fallback к env через assert_write_access_fn (имеет ту же логику)
        try:
            ctx.assert_write_access_fn(x_krab_web_key, token)
            return
        except HTTPException:
            raise
    bearer = ""
    if authorization:
        parts = authorization.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer = parts[1].strip()
    if bearer and bearer == expected:
        return
    # Иначе пускаем legacy validation
    ctx.assert_write_access_fn(x_krab_web_key, token)


def build_mcp_gateway_router(ctx: RouterContext) -> APIRouter:
    """Factory: возвращает APIRouter с MCP gateway endpoints.

    Endpoints совместимы с MCP spec (2024-11-05) для HTTP-транспорта.
    """
    router = APIRouter(tags=["mcp_gateway"])

    # ── Server info ─────────────────────────────────────────────────────────

    @router.get("/api/mcp/server/info")
    async def mcp_server_info() -> dict[str, Any]:
        """Возвращает MCP server metadata (handshake)."""
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
            },
            "instructions": (
                "Krab MCP gateway — exposes all userbot tools "
                "(MCP relay + native peekaboo/web_search/tor_fetch/voice/userbot/vpn)."
            ),
        }

    # ── Tools listing ───────────────────────────────────────────────────────

    @router.post("/api/mcp/tools/list")
    async def mcp_tools_list(payload: dict | None = Body(default=None)) -> dict[str, Any]:
        """Список всех экспонируемых tool definitions в MCP-native format."""
        del payload  # MCP клиенты могут слать {} — игнорируем тело.
        tools = await _list_mcp_tools(ctx)
        return {"tools": tools}

    # GET-вариант — удобство для browser / curl без -X POST.
    @router.get("/api/mcp/tools/list")
    async def mcp_tools_list_get() -> dict[str, Any]:
        tools = await _list_mcp_tools(ctx)
        return {"tools": tools}

    # ── Tool execution ──────────────────────────────────────────────────────

    @router.post("/api/mcp/tools/call")
    async def mcp_tools_call(
        payload: dict = Body(...),
        authorization: str = Header(default="", alias="Authorization"),
        x_krab_web_key: str = Header(default="", alias="X-Krab-Web-Key"),
        token: str = Query(default=""),
    ) -> dict[str, Any]:
        """Выполняет MCP tool по имени.

        Body: ``{"name": str, "arguments": dict}``
        Возвращает: ``{"content": [{"type": "text", "text": str}], "isError": bool}``.
        """
        _check_bearer_or_legacy(
            ctx,
            authorization=authorization,
            x_krab_web_key=x_krab_web_key,
            token=token,
        )
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid_payload")
        name = str(payload.get("name", "")).strip()
        if not name:
            raise HTTPException(status_code=400, detail="missing_tool_name")
        arguments = payload.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise HTTPException(status_code=400, detail="arguments_must_be_object")

        mgr = _resolve_mcp_manager(ctx)
        try:
            result = await mgr.call_tool_unified(name, arguments)
        except TimeoutError as exc:
            return {
                "content": [{"type": "text", "text": f"⏱ timeout: {exc}"}],
                "isError": True,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"❌ error: {exc}"}],
                "isError": True,
            }

        text = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False, default=str)
        )
        is_error = isinstance(text, str) and text.startswith("❌")
        return {
            "content": [{"type": "text", "text": text}],
            "isError": bool(is_error),
        }

    # ── SSE streaming endpoint ──────────────────────────────────────────────

    async def _sse_event_stream(
        keepalive_interval: float = 15.0,
    ) -> AsyncIterator[str]:
        """SSE поток: сначала шлёт server/info как `endpoint` event,
        затем периодические `ping` для keepalive."""
        info = {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocolVersion": MCP_PROTOCOL_VERSION,
        }
        yield "event: endpoint\n"
        yield f"data: {json.dumps(info, ensure_ascii=False)}\n\n"
        # tools snapshot once
        try:
            tools = await _list_mcp_tools(ctx)
            yield "event: tools\n"
            yield f"data: {json.dumps({'tools': tools}, ensure_ascii=False)}\n\n"
        except HTTPException as exc:
            yield "event: error\n"
            yield f"data: {json.dumps({'error': str(exc.detail)})}\n\n"

        # keepalive loop (cap iterations to limit by env, default ~unlimited)
        max_seconds = int(os.environ.get("KRAB_MCP_SSE_MAX_SECONDS", "0").strip() or "0")
        started = time.time()
        while True:
            await asyncio.sleep(keepalive_interval)
            yield f": ping {int(time.time())}\n\n"
            if max_seconds and (time.time() - started) >= max_seconds:
                break

    @router.get("/api/mcp/sse")
    async def mcp_sse(request: Request) -> StreamingResponse:
        """Server-Sent Events endpoint для MCP клиентов с push-моделью."""

        async def _wrapped() -> AsyncIterator[bytes]:
            try:
                async for chunk in _sse_event_stream():
                    if await request.is_disconnected():
                        break
                    yield chunk.encode("utf-8")
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            _wrapped(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── Admin HTML page ─────────────────────────────────────────────────────

    @router.get("/admin/mcp", response_class=HTMLResponse)
    async def admin_mcp_page() -> HTMLResponse:
        """HTML страница с tool list и test runner."""
        try:
            tools = await _list_mcp_tools(ctx)
        except HTTPException as exc:
            tools = []
            err = str(exc.detail)
        else:
            err = ""

        rows: list[str] = []
        for t in tools:
            schema_json = json.dumps(t.get("inputSchema", {}), ensure_ascii=False, indent=2)
            rows.append(
                "<tr>"
                f"<td><code>{_escape(t.get('name', ''))}</code></td>"
                f"<td>{_escape(t.get('description', ''))}</td>"
                f"<td><pre>{_escape(schema_json)}</pre></td>"
                "</tr>"
            )
        rows_html = "\n".join(rows) or (
            '<tr><td colspan="3"><em>No tools available — '
            "MCP relay не запущен или manifest пустой.</em></td></tr>"
        )
        err_html = f'<p style="color:red">⚠ {_escape(err)}</p>' if err else ""

        body = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Krab MCP gateway</title>
<style>
body {{ font-family: -apple-system, system-ui, sans-serif; margin: 24px; max-width: 1200px; }}
h1 {{ font-size: 22px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 8px; vertical-align: top; text-align: left; }}
th {{ background: #f4f4f4; }}
pre {{ background: #f9f9f9; padding: 6px; max-width: 480px; overflow:auto; font-size: 11px; }}
.controls {{ background:#fafafa; padding:12px; border:1px solid #ddd; margin-bottom:16px; }}
.controls input, .controls textarea {{ width: 95%; font-family: monospace; }}
#result {{ background:#0d1117; color:#c9d1d9; padding:12px; border-radius:6px;
          font-family: monospace; white-space: pre-wrap; min-height:60px; }}
</style>
</head>
<body>
<h1>🦀 Krab MCP gateway</h1>
{err_html}
<p>Endpoint base: <code>http://127.0.0.1:8080/api/mcp/</code> ·
SSE: <code>/api/mcp/sse</code> · protocol <code>{MCP_PROTOCOL_VERSION}</code></p>
<p>Tools exposed: <strong>{len(tools)}</strong></p>

<div class="controls">
  <h3>Test runner</h3>
  <label>Tool name: <input id="tool_name" placeholder="web_search" /></label><br><br>
  <label>Arguments (JSON):<br>
    <textarea id="tool_args" rows="4">{{"query": "hello"}}</textarea>
  </label><br>
  <label>Bearer token (optional):
    <input id="bearer" placeholder="WEB_API_KEY" />
  </label><br><br>
  <button onclick="runTool()">Call tool</button>
</div>
<div id="result">Output appears here.</div>

<h2>Available tools</h2>
<table>
<thead><tr><th>Name</th><th>Description</th><th>Schema</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>

<script>
async function runTool() {{
  const name = document.getElementById('tool_name').value.trim();
  const argsStr = document.getElementById('tool_args').value.trim() || '{{}}';
  const bearer = document.getElementById('bearer').value.trim();
  let args;
  try {{ args = JSON.parse(argsStr); }} catch (e) {{
    document.getElementById('result').textContent = 'invalid JSON: ' + e;
    return;
  }}
  const headers = {{'Content-Type': 'application/json'}};
  if (bearer) headers['Authorization'] = 'Bearer ' + bearer;
  document.getElementById('result').textContent = '⏳ calling...';
  try {{
    const r = await fetch('/api/mcp/tools/call', {{
      method: 'POST',
      headers: headers,
      body: JSON.stringify({{name: name, arguments: args}})
    }});
    const j = await r.json();
    document.getElementById('result').textContent =
      'HTTP ' + r.status + '\\n' + JSON.stringify(j, null, 2);
  }} catch (e) {{
    document.getElementById('result').textContent = 'fetch error: ' + e;
  }}
}}
</script>
</body>
</html>
"""
        return HTMLResponse(content=body)

    return router


def _escape(s: Any) -> str:
    """Минимальный HTML-escape для inline вывода."""
    text = str(s)
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


__all__ = ["build_mcp_gateway_router"]
