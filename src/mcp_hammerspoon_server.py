# -*- coding: utf-8 -*-
"""
Krab Hammerspoon MCP Server.

Экспонирует управление окнами macOS через HammerspoonBridge как MCP tools.
Позволяет Claude фокусировать приложения, изменять расположение окон и
применять preset-раскладки без shell round-trip через bash.

### MVP scope

Только **управление окнами**. Сознательно не включены деструктивные операции
(закрытие приложений, системные события) — для таких операций используй
macos_automation.py напрямую через Telegram-команду.

### Usage (standalone)

```bash
venv/bin/python -m src.mcp_hammerspoon_server
```

Сервер общается через stdio (MCP standard). Claude Desktop запустит
автоматически при старте сессии если зарегистрирован в config.

### Claude Desktop registration

Добавить в `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "krab-hammerspoon": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python",
      "args": ["-m", "src.mcp_hammerspoon_server"],
      "cwd": "/Users/pablito/Antigravity_AGENTS/Краб"
    }
  }
}
```

### Environment variables

- `HS_BASE_URL` — override base URL (default `http://localhost:10101`)
- `HS_TIMEOUT_SEC` — HTTP timeout в секундах (default `5.0`)
- `HS_PASS_KEY` — опциональный X-Krab-Pass header
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from src.integrations.hammerspoon_bridge import HammerspoonBridge, HammerspoonBridgeError

# --- Configuration -------------------------------------------------------

_BASE_URL = os.environ.get("HS_BASE_URL", "http://localhost:10101")
_TIMEOUT_SEC = float(os.environ.get("HS_TIMEOUT_SEC", "5.0"))
_PASS_KEY = os.environ.get("HS_PASS_KEY")

mcp = FastMCP("krab-hammerspoon")

# Один инстанс bridge на весь процесс (не синглтон модульного уровня — настройки
# могут отличаться от дефолтного hammerspoon из hammerspoon_bridge.py)
_bridge = HammerspoonBridge(base_url=_BASE_URL, timeout=_TIMEOUT_SEC, pass_key=_PASS_KEY)


# --- Internal helpers ----------------------------------------------------


def _wrap_error(exc: Exception) -> dict[str, Any]:
    """Преобразует исключение в error-dict, который Claude читает без traceback."""
    return {
        "_error": type(exc).__name__,
        "message": str(exc),
        "hint": "Убедись что Hammerspoon запущен и krab-hs HTTP server активен (порт 10101).",
    }


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
def hs_is_available() -> dict[str, Any]:
    """
    Быстрая проверка доступности Hammerspoon (TCP connect, ~1 ms).

    Возвращает: {"available": bool}. Не делает HTTP-запроса — только проверяет
    что порт 10101 слушает. Используй первым делом перед другими hs_* tools.
    """
    return {"available": _bridge.is_available()}


@mcp.tool()
def hs_status() -> dict[str, Any]:
    """
    Статус Hammerspoon: версия, количество экранов, базовые метаданные.

    Возвращает dict с полями version, screens и пр. При недоступности
    возвращает dict с полем _error.
    """
    try:
        return _run(_bridge.status())
    except HammerspoonBridgeError as exc:
        return _wrap_error(exc)


@mcp.tool()
def hs_list_windows() -> dict[str, Any]:
    """
    Список всех видимых окон: id, title, app для каждого.

    Возвращает: {"windows": [...]}. Используй чтобы узнать точные имена
    приложений перед вызовом hs_focus_app или hs_move_window.
    """
    try:
        windows = _run(_bridge.list_windows())
        return {"windows": list(windows)}
    except HammerspoonBridgeError as exc:
        return _wrap_error(exc)


@mcp.tool()
def hs_focus_app(app: str) -> dict[str, Any]:
    """
    Сфокусировать главное окно приложения.

    Args:
        app: имя приложения (например "Terminal", "Safari", "Visual Studio Code").
             Смотри hs_list_windows() для точного списка.
    """
    try:
        return _run(_bridge.focus(app))
    except HammerspoonBridgeError as exc:
        return _wrap_error(exc)


@mcp.tool()
def hs_move_window(app: str, x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """
    Переместить и изменить размер окна приложения.

    Координаты принимаются как доля экрана (0.0..1.0) ИЛИ абсолютные пиксели
    (> 2). Lua-код в Hammerspoon init.lua разрешает режим автоматически.

    Args:
        app: имя приложения (пустая строка = frontmost window)
        x: left edge (доля от 0.0 до 1.0, или пиксели)
        y: top edge
        w: ширина
        h: высота
    """
    try:
        return _run(_bridge.move(app=app, x=x, y=y, w=w, h=h))
    except HammerspoonBridgeError as exc:
        return _wrap_error(exc)


@mcp.tool()
def hs_tile(preset: str, app: str = "") -> dict[str, Any]:
    """
    Применить preset-раскладку окна через Hammerspoon moveToUnit().

    Надёжнее пиксельного позиционирования при нескольких мониторах разного DPI.

    Args:
        preset: one of "left", "right", "top", "bottom", "full"
        app: имя приложения (пустая строка = frontmost window)
    """
    try:
        return _run(_bridge.tile(preset=preset, app=app))
    except HammerspoonBridgeError as exc:
        return _wrap_error(exc)


# --- Entry point ---------------------------------------------------------


def main() -> None:
    """MCP server entry point.

    Transport выбирается через env:
      MCP_TRANSPORT=stdio (default) — Claude Desktop spawn
      MCP_TRANSPORT=sse              — LaunchAgent на :MCP_PORT (default 8013)
    """
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport == "sse":
        # FastMCP читает host/port из своих settings
        port = int(os.environ.get("MCP_PORT", "8013"))
        host = os.environ.get("MCP_HOST", "127.0.0.1")
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="sse")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
