# -*- coding: utf-8 -*-
"""
userbot_self_tools.py — нативные read-only инструменты Telegram userbot для LLM.

Экспортирует 6 tools, доступных Krab LLM напрямую (без внешнего MCP):
  userbot_read_chat_history  — последние N сообщений в чате
  userbot_search_chat        — поиск сообщений в одном чате
  userbot_search_global      — глобальный поиск по всем диалогам
  userbot_get_message        — получить конкретное сообщение
  userbot_get_dialogs        — список диалогов userbot
  userbot_resolve_user       — info о пользователе по username/id

Архитектура:
- `UserbotSelfTools` — wrapper вокруг pyrogram.Client (read-only).
- Singleton `userbot_self_tools` лениво подбирает client через
  `set_userbot_client(client)` (вызывается из `KraabUserbot.start()`)
  либо через атрибут `_userbot_client` модуля `src.userbot_bridge`.
- Все методы возвращают JSON-friendly dict; при отсутствии client —
  `{"ok": False, "error": "client_unavailable"}` (graceful for tests).
- Rate-limit: 10 вызовов / 60 секунд скользящим окном (per-instance).
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from structlog import get_logger

logger = get_logger(__name__)

# Лимит вызовов в скользящем окне, чтобы LLM не зацикливался на чтении истории.
_RATE_LIMIT_CALLS = 10
_RATE_LIMIT_WINDOW_SEC = 60.0


# ------------------------------------------------------------------
# Tool schemas (OpenAI/MCP-совместимый формат)
# ------------------------------------------------------------------

USERBOT_SELF_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "userbot_read_chat_history",
        "description": (
            "Read the last N messages from a Telegram chat as the Krab userbot. "
            "Use this when you need to recall what was said in a conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": ["integer", "string"],
                    "description": "Telegram chat id, username (@channel) or 'me'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many recent messages to fetch (1..100, default 20).",
                    "default": 20,
                },
                "offset_id": {
                    "type": "integer",
                    "description": "Start before this message id (0 = newest).",
                    "default": 0,
                },
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "userbot_search_chat",
        "description": (
            "Search messages in a single Telegram chat by keyword. "
            "Use when you need to find earlier mentions of a topic in this conversation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": ["integer", "string"],
                    "description": "Telegram chat id or username.",
                },
                "query": {"type": "string", "description": "Search keyword(s)."},
                "limit": {
                    "type": "integer",
                    "description": "Max results (1..100, default 20).",
                    "default": 20,
                },
            },
            "required": ["chat_id", "query"],
        },
    },
    {
        "name": "userbot_search_global",
        "description": (
            "Search messages across all Telegram chats the userbot is part of. "
            "Useful for recalling something discussed in a forgotten chat."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword(s)."},
                "limit": {
                    "type": "integer",
                    "description": "Max results (1..100, default 20).",
                    "default": 20,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "userbot_get_message",
        "description": "Fetch a single message by chat_id and message_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chat_id": {"type": ["integer", "string"]},
                "message_id": {"type": "integer"},
            },
            "required": ["chat_id", "message_id"],
        },
    },
    {
        "name": "userbot_get_dialogs",
        "description": (
            "List the userbot's recent dialogs (chats/users/channels) "
            "ordered by recency. Helpful to discover available chat_ids."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max dialogs (1..100, default 30).",
                    "default": 30,
                },
            },
            "required": [],
        },
    },
    {
        "name": "userbot_resolve_user",
        "description": (
            "Resolve a Telegram user by username (e.g. '@alice') or numeric id. "
            "Returns id, username, names, is_bot, is_self."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "username_or_id": {
                    "type": ["string", "integer"],
                    "description": "Telegram @username or numeric user id.",
                },
            },
            "required": ["username_or_id"],
        },
    },
]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _serialize_message(msg: Any) -> dict[str, Any]:
    """Превращает pyrogram.Message в JSON-friendly dict (минимум полей)."""
    if msg is None:
        return {}

    media_type: str | None = None
    has_media = False
    for media_attr in ("photo", "video", "voice", "audio", "document", "sticker", "animation"):
        if getattr(msg, media_attr, None) is not None:
            media_type = media_attr
            has_media = True
            break

    from_user = getattr(msg, "from_user", None)
    from_user_block: dict[str, Any] | None = None
    if from_user is not None:
        from_user_block = {
            "id": getattr(from_user, "id", None),
            "username": getattr(from_user, "username", None),
            "first_name": getattr(from_user, "first_name", None),
            "is_bot": getattr(from_user, "is_bot", None),
            "is_self": getattr(from_user, "is_self", None),
        }

    chat = getattr(msg, "chat", None)
    chat_id = getattr(chat, "id", None) if chat is not None else None

    date_obj = getattr(msg, "date", None)
    date_iso = None
    if date_obj is not None:
        try:
            date_iso = date_obj.isoformat()
        except AttributeError:
            date_iso = str(date_obj)

    return {
        "id": getattr(msg, "id", None),
        "chat_id": chat_id,
        "from_user": from_user_block,
        "text": getattr(msg, "text", None) or getattr(msg, "caption", None) or "",
        "date_iso": date_iso,
        "has_media": has_media,
        "media_type": media_type,
        "reply_to_message_id": getattr(msg, "reply_to_message_id", None),
    }


def _serialize_dialog(dlg: Any) -> dict[str, Any]:
    """pyrogram.Dialog → dict."""
    chat = getattr(dlg, "chat", None)
    return {
        "chat_id": getattr(chat, "id", None) if chat is not None else None,
        "type": str(getattr(getattr(chat, "type", None), "name", "")) or None,
        "title": getattr(chat, "title", None),
        "username": getattr(chat, "username", None),
        "first_name": getattr(chat, "first_name", None),
        "unread_count": getattr(dlg, "unread_messages_count", None),
        "top_message_id": getattr(getattr(dlg, "top_message", None), "id", None),
    }


def _serialize_user(user: Any) -> dict[str, Any]:
    if user is None:
        return {}
    return {
        "id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
        "first_name": getattr(user, "first_name", None),
        "last_name": getattr(user, "last_name", None),
        "is_bot": getattr(user, "is_bot", None),
        "is_self": getattr(user, "is_self", None),
        "is_verified": getattr(user, "is_verified", None),
        "is_premium": getattr(user, "is_premium", None),
    }


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    """Безопасное приведение к int с обрезкой в [lo, hi]."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


# ------------------------------------------------------------------
# UserbotSelfTools — основной класс
# ------------------------------------------------------------------


class UserbotSelfTools:
    """
    Read-only Telegram tools на pyrogram.Client.

    Все методы async; при отсутствии client возвращают graceful error-dict
    (не бросают), чтобы LLM мог корректно отреагировать.
    """

    def __init__(self, client: Any | None = None) -> None:
        self._client: Any | None = client
        # Сколь��ящее окно временных меток для rate-limit.
        self._calls: deque[float] = deque(maxlen=_RATE_LIMIT_CALLS * 4)

    # ---- Инжекция клиента ------------------------------------------------

    def set_client(self, client: Any | None) -> None:
        """Устанавливает pyrogram.Client (вызывается из KraabUserbot.start)."""
        self._client = client
        logger.info("userbot_self_tools_client_set", has_client=bool(client))

    def _resolve_client(self) -> Any | None:
        """Лениво пытается достать client, если ещё не подвязан."""
        if self._client is not None:
            return self._client
        try:
            from .. import userbot_bridge as _bridge  # type: ignore[import-not-found]

            candidate = getattr(_bridge, "_userbot_client", None)
            if candidate is not None:
                self._client = candidate
        except ImportError:
            return None
        return self._client

    # ---- Rate-limit ------------------------------------------------------

    def _check_rate_limit(self) -> bool:
        """True если можно вызывать; False — превышен лимит."""
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_SEC
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()
        if len(self._calls) >= _RATE_LIMIT_CALLS:
            return False
        self._calls.append(now)
        return True

    @staticmethod
    def _new_request_id() -> str:
        return uuid.uuid4().hex[:8]

    def _guard(self, tool: str) -> dict[str, Any] | None:
        """Возвращает error-dict если нельзя вызывать; иначе None."""
        client = self._resolve_client()
        if client is None:
            logger.warning("userbot_self_tool_no_client", tool=tool)
            return {"ok": False, "error": "client_unavailable", "tool": tool}
        if not self._check_rate_limit():
            logger.warning("userbot_self_tool_rate_limited", tool=tool)
            return {"ok": False, "error": "rate_limited", "tool": tool}
        return None

    # ---- Tools -----------------------------------------------------------

    async def read_chat_history(
        self,
        chat_id: int | str,
        limit: int = 20,
        offset_id: int = 0,
    ) -> dict[str, Any]:
        guard = self._guard("userbot_read_chat_history")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        limit = _clamp(limit, 1, 100, 20)
        try:
            offset_id = max(0, int(offset_id))
        except (TypeError, ValueError):
            offset_id = 0

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_read_chat_history",
            request_id=request_id,
            chat_id=chat_id,
            limit=limit,
            offset_id=offset_id,
        )
        client = self._client
        try:
            messages: list[dict[str, Any]] = []
            async for msg in client.get_chat_history(
                chat_id=chat_id, limit=limit, offset_id=offset_id
            ):
                messages.append(_serialize_message(msg))
            return {
                "ok": True,
                "request_id": request_id,
                "chat_id": chat_id,
                "count": len(messages),
                "messages": messages,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_read_chat_history",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    async def search_chat(
        self,
        chat_id: int | str,
        query: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        guard = self._guard("userbot_search_chat")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "error": "empty_query"}
        limit = _clamp(limit, 1, 100, 20)

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_search_chat",
            request_id=request_id,
            chat_id=chat_id,
            query_len=len(query),
            limit=limit,
        )
        client = self._client
        try:
            messages: list[dict[str, Any]] = []
            async for msg in client.search_messages(chat_id=chat_id, query=query, limit=limit):
                messages.append(_serialize_message(msg))
            return {
                "ok": True,
                "request_id": request_id,
                "chat_id": chat_id,
                "query": query,
                "count": len(messages),
                "messages": messages,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_search_chat",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    async def search_global(self, query: str, limit: int = 20) -> dict[str, Any]:
        guard = self._guard("userbot_search_global")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "error": "empty_query"}
        limit = _clamp(limit, 1, 100, 20)

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_search_global",
            request_id=request_id,
            query_len=len(query),
            limit=limit,
        )
        client = self._client
        try:
            messages: list[dict[str, Any]] = []
            async for msg in client.search_global(query=query, limit=limit):
                messages.append(_serialize_message(msg))
            return {
                "ok": True,
                "request_id": request_id,
                "query": query,
                "count": len(messages),
                "messages": messages,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_search_global",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    async def get_message(self, chat_id: int | str, message_id: int) -> dict[str, Any]:
        guard = self._guard("userbot_get_message")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        try:
            message_id = int(message_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid_message_id"}

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_get_message",
            request_id=request_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        client = self._client
        try:
            msg = await client.get_messages(chat_id=chat_id, message_ids=message_id)
            return {
                "ok": True,
                "request_id": request_id,
                "message": _serialize_message(msg),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_get_message",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    async def get_dialogs(self, limit: int = 30) -> dict[str, Any]:
        guard = self._guard("userbot_get_dialogs")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        limit = _clamp(limit, 1, 100, 30)

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_get_dialogs",
            request_id=request_id,
            limit=limit,
        )
        client = self._client
        try:
            dialogs: list[dict[str, Any]] = []
            async for dlg in client.get_dialogs(limit=limit):
                dialogs.append(_serialize_dialog(dlg))
            return {
                "ok": True,
                "request_id": request_id,
                "count": len(dialogs),
                "dialogs": dialogs,
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_get_dialogs",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

    async def resolve_user(self, username_or_id: int | str) -> dict[str, Any]:
        guard = self._guard("userbot_resolve_user")
        if guard is not None:
            return guard

        request_id = self._new_request_id()
        if username_or_id in (None, ""):
            return {"ok": False, "error": "empty_identifier"}

        logger.info(
            "userbot_self_tool_call",
            tool="userbot_resolve_user",
            request_id=request_id,
            identifier=str(username_or_id)[:64],
        )
        client = self._client
        try:
            user = await client.get_users(username_or_id)
            return {
                "ok": True,
                "request_id": request_id,
                "user": _serialize_user(user),
            }
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "userbot_self_tool_error",
                tool="userbot_resolve_user",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


# ------------------------------------------------------------------
# Singleton + dispatcher
# ------------------------------------------------------------------

userbot_self_tools = UserbotSelfTools()


def set_userbot_client(client: Any | None) -> None:
    """Подвязать pyrogram.Client к синглтону (вызывается из KraabUserbot.start)."""
    userbot_self_tools.set_client(client)


_TOOL_HANDLERS = {
    "userbot_read_chat_history": lambda args: userbot_self_tools.read_chat_history(
        chat_id=args.get("chat_id"),
        limit=args.get("limit", 20),
        offset_id=args.get("offset_id", 0),
    ),
    "userbot_search_chat": lambda args: userbot_self_tools.search_chat(
        chat_id=args.get("chat_id"),
        query=args.get("query", ""),
        limit=args.get("limit", 20),
    ),
    "userbot_search_global": lambda args: userbot_self_tools.search_global(
        query=args.get("query", ""),
        limit=args.get("limit", 20),
    ),
    "userbot_get_message": lambda args: userbot_self_tools.get_message(
        chat_id=args.get("chat_id"),
        message_id=args.get("message_id"),
    ),
    "userbot_get_dialogs": lambda args: userbot_self_tools.get_dialogs(
        limit=args.get("limit", 30),
    ),
    "userbot_resolve_user": lambda args: userbot_self_tools.resolve_user(
        username_or_id=args.get("username_or_id"),
    ),
}


async def dispatch_userbot_self_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    Вызвать tool по имени. Возвращает JSON-friendly dict.
    Используется из mcp_client.call_tool_unified().
    """
    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is None:
        return {"ok": False, "error": f"unknown_tool:{tool_name}"}
    if not isinstance(arguments, dict):
        arguments = {}
    return await handler(arguments)


def is_userbot_self_tool(tool_name: str) -> bool:
    """True если tool_name принадлежит набору userbot_self_tools."""
    return tool_name in _TOOL_HANDLERS
