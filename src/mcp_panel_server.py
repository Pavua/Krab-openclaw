# -*- coding: utf-8 -*-
"""
Krab Panel API MCP Server.

Экспонирует read-only `/api/*` endpoints из `src/modules/web_app.py` как MCP tools.
Нужен чтобы Claude мог быстро запрашивать runtime состояние Krab без `curl` через
Bash (overhead, timeout handling, JSON parsing вручную).

### MVP scope

Только **read** endpoints. Write endpoints (`/api/notify`, `/api/*/update`, `/api/*/remediate`)
намеренно не включены — защищает от случайной мутации runtime state через MCP вызов.
Для write operations используй Telegram команды или curl с правильным `WEB_API_KEY`.

### Usage (standalone)

```bash
venv/bin/python -m src.mcp_panel_server
```

Сервер общается через stdio (MCP standard). Запускать руками не нужно — Claude Desktop
запустит автоматически при старте сессии если зарегистрирован в config.

### Claude Desktop registration

Добавить в `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "krab-panel": {
      "command": "/Users/pablito/Antigravity_AGENTS/Краб/venv/bin/python",
      "args": ["-m", "src.mcp_panel_server"],
      "cwd": "/Users/pablito/Antigravity_AGENTS/Краб"
    }
  }
}
```

### Environment variables

- `KRAB_PANEL_URL` — override base URL (default `http://127.0.0.1:8080`)
- `KRAB_PANEL_TIMEOUT_SEC` — HTTP timeout в секундах (default `5.0`)
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

# --- Configuration -------------------------------------------------------

PANEL_BASE_URL = os.environ.get("KRAB_PANEL_URL", "http://127.0.0.1:8080")
HTTP_TIMEOUT_SEC = float(os.environ.get("KRAB_PANEL_TIMEOUT_SEC", "5.0"))

mcp = FastMCP("krab-panel")


# --- Internal helpers ----------------------------------------------------


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Internal GET helper с единообразной обработкой ошибок.

    Возвращает dict с данными response ИЛИ dict с полем `_error`, если что-то
    пошло не так. Caller (Claude) может проверить `result.get("_error")` чтобы
    различить успех от ошибки. Это цивилизованный fallback вместо raise —
    raise в MCP tool перевёл бы ошибку в user-visible error с traceback'ом.
    """
    url = f"{PANEL_BASE_URL}{path}"
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SEC) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "_error": "http_error",
            "status_code": exc.response.status_code,
            "url": url,
            "hint": "Check if Krab panel is up (curl http://127.0.0.1:8080/api/health/lite).",
        }
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        return {
            "_error": "connection_failed",
            "message": str(exc),
            "url": url,
            "hint": "Krab likely not running. Start via 'new start_krab.command'.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "_error": "unexpected",
            "error_type": type(exc).__name__,
            "message": str(exc),
            "url": url,
        }


# --- Tools ---------------------------------------------------------------


@mcp.tool()
def krab_panel_health_lite() -> dict[str, Any]:
    """
    Получает lightweight health snapshot от Krab owner panel.

    Возвращает: status, telegram_session_state, telegram_userbot_state,
    openclaw_auth_state, last_runtime_route, inbox_summary, voice_gateway_configured.

    Это быстрый health check (<100ms при running Krab). Используй первым делом
    в начале каждой сессии и после рестартов. Для полного health используй
    krab_panel_health_full().
    """
    return _get("/api/health/lite")


@mcp.tool()
def krab_panel_health_full() -> dict[str, Any]:
    """
    Полный health snapshot включая LM Studio state, OpenClaw models, runtime truth,
    VRAM usage, все сервисы. Медленнее чем lite (до 1-2 секунд) — используй только
    когда реально нужны детали про каждую подсистему.
    """
    return _get("/api/health")


@mcp.tool()
def krab_panel_stats() -> dict[str, Any]:
    """
    Общая runtime статистика: message counts per-chat, total requests, errors
    by type, uptime. Полезно для проверки активности за сессию.
    """
    return _get("/api/stats")


@mcp.tool()
def krab_panel_voice_runtime() -> dict[str, Any]:
    """
    Текущий voice runtime profile: enabled (on/off), delivery mode
    (text+voice / voice-only), voice id (edge-tts), speed, blocked_chats список.

    Эквивалент `!voice status` команды Telegram, но без Telegram round-trip.
    """
    return _get("/api/voice/runtime")


@mcp.tool()
def krab_panel_openclaw_config() -> dict[str, Any]:
    """
    OpenClaw runtime routing config: primary model, fallback chain, providers
    enabled state, thinking mode, last_runtime_route (detail последнего ответа).
    """
    return _get("/api/openclaw/runtime-config")


@mcp.tool()
def krab_panel_inbox_status() -> dict[str, Any]:
    """
    Inbox service summary: total items, open items, fresh/stale counts,
    open escalations, pending approvals, new owner mentions, pending reminders.
    """
    return _get("/api/inbox/status")


@mcp.tool()
def krab_panel_inbox_items(limit: int = 20, kind: str | None = None) -> dict[str, Any]:
    """
    Список inbox items.

    Args:
        limit: сколько вернуть (default 20, max ~100 по server-side ограничениям)
        kind: опциональный фильтр по item kind (e.g. "proactive_action",
              "incoming_message", "cron_run")
    """
    params: dict[str, Any] = {"limit": limit}
    if kind:
        params["kind"] = kind
    return _get("/api/inbox/items", params=params)


@mcp.tool()
def krab_panel_cron_jobs() -> dict[str, Any]:
    """
    Список OpenClaw cron jobs: name, schedule (cron expression), last_run_at,
    next_run_at, enabled state, last_status.
    """
    return _get("/api/openclaw/cron/jobs")


@mcp.tool()
def krab_panel_policy_matrix() -> dict[str, Any]:
    """
    Runtime access control matrix: ACL subjects (owner/full/partial/guest),
    commands accessible per level, owner-only commands list.

    Полезно чтобы понять почему команда была denied или кто имеет access.
    """
    return _get("/api/policy/matrix")


@mcp.tool()
def krab_panel_queue() -> dict[str, Any]:
    """
    Current message processing queue state: pending counts per-chat, active
    background tasks. Показывает если Krab перегружен или blocked на чём-то.
    """
    return _get("/api/queue")


@mcp.tool()
def krab_panel_mood(chat_id: str) -> dict[str, Any]:
    """
    Mood/sentiment snapshot для конкретного чата (rolling window reaction stats).

    Args:
        chat_id: chat id (отрицательный для supergroups/channels, положительный для private)
    """
    return _get(f"/api/mood/{chat_id}")


# --- Entry point ---------------------------------------------------------


def main() -> None:
    """MCP server stdio entry point. Запускается Claude Desktop автоматически."""
    mcp.run()


if __name__ == "__main__":
    main()
