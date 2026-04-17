# -*- coding: utf-8 -*-
"""
voice_assistant_tools.py — MCP инструменты для Voice Assistant Mode (VA Phase 1.4).

Инструменты:
  voice:get_recent_dictations  — последние N транскрипций из Krab Ear
  voice:send_telegram          — отправить сообщение в Telegram через userbot
  voice:search_memory          — поиск по долгосрочной памяти (MemoryManager)

Каждый инструмент возвращает dict, совместимый с MCP tool result schema.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger(__name__)


# ------------------------------------------------------------------
# Tool definitions (OpenClaw / MCP schema)
# ------------------------------------------------------------------

VOICE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "voice:get_recent_dictations",
        "description": (
            "Fetch the last N transcription items from Krab Ear history. "
            "Useful for voice assistant context: know what the user dictated recently."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of recent items to fetch (default: 5, max: 20).",
                    "default": 5,
                },
            },
            "required": [],
        },
    },
    {
        "name": "voice:send_telegram",
        "description": (
            "Send a Telegram message via the Krab userbot. "
            "Use when the voice assistant should forward information or a reply to a Telegram chat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Telegram chat ID or username (e.g., 'me', '123456', '@channel').",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send.",
                },
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "voice:search_memory",
        "description": (
            "Search long-term memory for facts relevant to the voice query. "
            "Wraps MemoryManager.recall() (ChromaDB vector search)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (natural language).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
]


# ------------------------------------------------------------------
# Tool implementations
# ------------------------------------------------------------------


async def voice_get_recent_dictations(
    n: int = 5,
    *,
    krab_ear_socket_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Получить последние N транскрипций из Krab Ear через IPC socket.

    Использует тот же unix-socket протокол JSON-RPC, что и KrabEarClient.
    При недоступности backend возвращает graceful error (не кидает).
    """
    import os

    n = max(1, min(int(n), 20))
    socket_path = krab_ear_socket_path or os.path.expanduser(
        "~/Library/Application Support/KrabEar/krabear.sock"
    )

    request_payload = json.dumps(
        {
            "id": "voice_tool_history",
            "method": "get_history",
            "params": {"limit": n, "offset": 0},
        },
        ensure_ascii=False,
    )

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path=socket_path),
            timeout=3.0,
        )
        writer.write((request_payload + "\n").encode("utf-8"))
        await writer.drain()

        raw = await asyncio.wait_for(reader.readline(), timeout=3.0)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

        response = json.loads(raw.decode("utf-8", errors="replace"))
        if not response.get("ok"):
            return {
                "ok": False,
                "error": response.get("error", {}).get("message", "ipc_error"),
                "items": [],
            }

        items = response.get("result", {}).get("items", [])
        # Нормализуем к минимальному контракту: text + ts
        normalized = [
            {
                "text": item.get("text", ""),
                "ts": item.get("ts") or item.get("timestamp", ""),
                "language": item.get("language", ""),
            }
            for item in items[:n]
        ]
        return {"ok": True, "items": normalized, "count": len(normalized)}

    except FileNotFoundError:
        logger.warning("voice_tool_krab_ear_socket_missing", path=socket_path)
        return {"ok": False, "error": "krab_ear_socket_not_found", "items": []}
    except (asyncio.TimeoutError, ConnectionRefusedError) as exc:
        logger.warning("voice_tool_krab_ear_ipc_timeout", error=str(exc))
        return {"ok": False, "error": f"krab_ear_ipc_timeout: {exc}", "items": []}
    except Exception as exc:  # noqa: BLE001
        logger.error("voice_tool_krab_ear_ipc_error", error=str(exc))
        return {"ok": False, "error": str(exc), "items": []}


async def voice_send_telegram(
    chat_id: str,
    text: str,
    *,
    telegram_client: Any = None,
) -> Dict[str, Any]:
    """
    Отправить сообщение в Telegram через userbot.

    В production telegram_client — это pyrogram.Client singleton из userbot_bridge.
    В тестах подменяется mock'ом.
    """
    if not chat_id or not text:
        return {"ok": False, "error": "chat_id and text are required"}

    if telegram_client is None:
        # Ленивый импорт синглтона, чтобы не создавать circular import на уровне модуля.
        try:
            from ..userbot_bridge import _userbot_client  # type: ignore[attr-defined]

            telegram_client = _userbot_client
        except ImportError:
            return {"ok": False, "error": "telegram_client_not_available"}

    if telegram_client is None:
        return {"ok": False, "error": "telegram_client_not_initialized"}

    try:
        await telegram_client.send_message(chat_id, text)
        logger.info("voice_tool_telegram_sent", chat_id=chat_id, text_len=len(text))
        return {"ok": True, "chat_id": chat_id, "text_len": len(text)}
    except Exception as exc:  # noqa: BLE001
        logger.error("voice_tool_telegram_send_error", chat_id=chat_id, error=str(exc))
        return {"ok": False, "error": str(exc)}


def voice_search_memory(
    query: str,
    limit: int = 5,
    *,
    memory_manager: Any = None,
) -> Dict[str, Any]:
    """
    Поиск в долгосрочной памяти (ChromaDB) по запросу.

    Оборачивает MemoryManager.recall() с нормализацией результата.
    Синхронный — recall() тоже синхронный (ChromaDB blocking API).
    """
    if not query.strip():
        return {"ok": False, "error": "query is empty", "results": ""}

    if memory_manager is None:
        try:
            from ..memory_engine import memory_manager as _mm

            memory_manager = _mm
        except ImportError:
            return {"ok": False, "error": "memory_manager_not_available", "results": ""}

    limit = max(1, min(int(limit), 20))
    try:
        results = memory_manager.recall(query, n_results=limit)
        logger.debug("voice_tool_memory_search", query=query[:50], found=bool(results))
        return {"ok": True, "results": results, "query": query}
    except Exception as exc:  # noqa: BLE001
        logger.error("voice_tool_memory_search_error", error=str(exc))
        return {"ok": False, "error": str(exc), "results": ""}


# ------------------------------------------------------------------
# Dispatcher — called by OpenClaw tool execution hook
# ------------------------------------------------------------------

_TOOL_HANDLERS = {
    "voice:get_recent_dictations": voice_get_recent_dictations,
    "voice:send_telegram": voice_send_telegram,
    "voice:search_memory": voice_search_memory,
}


async def dispatch_voice_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    **deps: Any,
) -> Dict[str, Any]:
    """
    Точка входа для OpenClaw tool hook.

    Args:
        tool_name:  Одно из VOICE_TOOL_SCHEMAS[*].name.
        arguments:  Параметры вызова инструмента.
        **deps:     Зависимости (telegram_client, memory_manager, krab_ear_socket_path).

    Returns:
        MCP-compatible result dict.
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"ok": False, "error": f"unknown_voice_tool: {tool_name}"}

    try:
        if asyncio.iscoroutinefunction(handler):
            return await handler(**{**arguments, **deps})
        else:
            return handler(**{**arguments, **deps})
    except TypeError as exc:
        logger.error("voice_tool_dispatch_error", tool=tool_name, error=str(exc))
        return {"ok": False, "error": f"tool_arg_error: {exc}"}
