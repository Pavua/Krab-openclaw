# -*- coding: utf-8 -*-
"""DEPRECATED Wave 50-B (2026-05-10).

Заменён на полноценный tor-full MCP в ``/Users/pablito/Antigravity_AGENTS/tor-mcp/``
(25 tools vs 3 tool subset здесь). Файл сохранён для:

- reference architecture (FastMCP wrapper над ``tor_bridge.py``);
- возможного fallback, если tor-full недоступен;
- ``tests/unit/test_mcp_tor_server.py`` всё ещё pass (regression safety).

Не запускается автоматически (LaunchAgent unloaded оркестратором). Manual run:

    venv/bin/python -m src.mcp_tor_server

Original Wave 44-Z docstring ниже сохранён без изменений ради archaeology.

---

Krab Tor MCP Server (Wave 44-Z).

Экспонирует Tor SOCKS5 daemon как MCP tools для агентного контура
(OpenClaw / Claude Desktop / Codex). Обёртка над ``src/integrations/tor_bridge.py``.

### Tools

- ``tor_status()`` — daemon health + текущий exit IP.
- ``tor_fetch(url, method, headers, timeout)`` — анонимный HTTP запрос.
- ``tor_check_exit_ip()`` — узнать текущий exit IP без полного fetch.

### Use-cases

Анонимные dev-ресёрч запросы (.onion-зеркала ищмений, blocked-by-region docs,
IP-rotation для тестов rate-limiter'ов). Ограничения по контенту — стандартные
legal use only; Tor MCP не делает исключений для запрещённого контента.

### Запуск

```bash
# stdio (Claude Desktop spawn)
venv/bin/python -m src.mcp_tor_server

# SSE (LaunchAgent на 8014)
MCP_TRANSPORT=sse MCP_PORT=8014 venv/bin/python -m src.mcp_tor_server
```

### Env vars

- ``TOR_SOCKS_PORT`` — SOCKS5 port (default ``9050``)
- ``MCP_TRANSPORT`` / ``MCP_PORT`` / ``MCP_HOST``
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.integrations import tor_bridge

# --- Configuration -------------------------------------------------------

_SOCKS_PORT = int(os.environ.get("TOR_SOCKS_PORT", "9050"))

mcp = FastMCP("krab-tor")


# --- Internal helpers ----------------------------------------------------


def _run(coro: Any) -> Any:
    """Запускает async-корутину из синхронного MCP tool.

    Robust к закрытому/отсутствующему event loop (тесты часто закрывают loop).
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# --- Tools ---------------------------------------------------------------


@mcp.tool()
def tor_status() -> dict[str, Any]:
    """
    Проверяет, что Tor daemon отвечает на SOCKS5 порту, и возвращает текущий exit IP.

    Returns: ``{"available": bool, "exit_ip": str | None, "error": str | None}``.
    Используй первым делом — если ``available=False``, остальные tools
    вернут ``{"ok": false, "error": "tor_not_running"}``.
    """
    result = _run(tor_bridge.health_check(socks_port=_SOCKS_PORT))
    return {
        "available": bool(result.get("ok")),
        "exit_ip": result.get("ip") or None,
        "error": result.get("error") or None,
    }


@mcp.tool()
def tor_check_exit_ip() -> dict[str, Any]:
    """
    Возвращает текущий exit IP Tor (через https://check.torproject.org/api/ip).

    Возвращает: ``{"ip": str | None}``. Полезно перед запросами, чтобы понять
    из какой страны идёт трафик, или после ``tor_new_circuit()`` (TODO) убедиться,
    что цепочка действительно сменилась.
    """
    ip = _run(tor_bridge.get_tor_ip(socks_port=_SOCKS_PORT))
    return {"ip": ip}


@mcp.tool()
def tor_fetch(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    Выполняет HTTP-запрос через Tor SOCKS5. Анонимный + DNS resolution через Tor.

    Args:
        url: целевой URL (включая ``.onion``)
        method: HTTP метод (default GET)
        headers: дополнительные заголовки
        timeout: read timeout в секундах (default 30, тор медленный)

    Returns:
        - ``{"ok": True, "status": int, "text": str (≤50KB), "url": str}``
        - ``{"ok": False, "error": str}`` если daemon не запущен или запрос упал

    Ограничения: text обрезается до 50KB; для больших ответов — несколько запросов
    с Range-заголовком. .onion-сайты регулярно нестабильны (timeout-retry ожидаем).
    """
    return _run(
        tor_bridge.tor_fetch(
            url,
            socks_port=_SOCKS_PORT,
            timeout=timeout,
            method=method,
            headers=headers,
        )
    )


# --- Entry point ---------------------------------------------------------


def main() -> None:
    """MCP server entry point.

    Transport через env:
      MCP_TRANSPORT=stdio (default) — Claude Desktop spawn
      MCP_TRANSPORT=sse              — LaunchAgent на :MCP_PORT (default 8014)
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse":
        port = int(os.environ.get("MCP_PORT", "8014"))
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
