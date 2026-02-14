# -*- coding: utf-8 -*-
"""
Telegram Chat Resolver.

Назначение:
1) Разрешать target чата для команд вроде summaryx.
2) Давать список недавних чатов для inline-picker в ЛС.
3) Централизовать нормализацию @username/chat_id/t.me ссылок.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any


_TME_RE = re.compile(r"^https?://t\.me/(?P<slug>[A-Za-z0-9_]{5,})/?$", re.IGNORECASE)


@dataclass(slots=True)
class ChatTarget:
    """Нормализованный target чата."""

    chat_id: int
    title: str
    chat_type: str


class TelegramChatResolver:
    """Утилита поиска/нормализации target-чата."""

    def __init__(self, black_box, max_picker_items: int = 8):
        self.black_box = black_box
        self.max_picker_items = max_picker_items

    @staticmethod
    def normalize_target(raw_target: str) -> str:
        """Нормализует пользовательский target в строковый идентификатор."""
        text = (raw_target or "").strip()
        if not text:
            return ""

        m = _TME_RE.match(text)
        if m:
            return f"@{m.group('slug')}"

        if text.startswith("@"):
            return text

        if text.lstrip("-").isdigit():
            return text

        return f"@{text}"

    async def resolve(self, client, raw_target: str) -> ChatTarget:
        """Разрешает target в реальный Telegram chat."""
        target = self.normalize_target(raw_target)
        if not target:
            raise ValueError("Пустой target чата")

        chat_ref: Any
        if target.lstrip("-").isdigit():
            chat_ref = int(target)
        else:
            chat_ref = target

        chat = await client.get_chat(chat_ref)
        title = chat.title or chat.first_name or chat.username or str(chat.id)
        chat_type = getattr(chat.type, "name", str(chat.type)).lower()
        return ChatTarget(chat_id=int(chat.id), title=str(title), chat_type=chat_type)

    def get_recent_chats(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Возвращает недавние чаты из BlackBox для picker в ЛС."""
        safe_limit = max(1, min(limit or self.max_picker_items, 20))
        db_path = getattr(self.black_box, "db_path", "")
        if not db_path:
            return []

        query = """
            SELECT
                chat_id,
                MAX(timestamp) AS last_ts,
                MAX(COALESCE(chat_title, 'Unknown')) AS chat_title
            FROM messages
            GROUP BY chat_id
            ORDER BY last_ts DESC
            LIMIT ?
        """
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(query, (safe_limit,)).fetchall()
        finally:
            conn.close()

        result: list[dict[str, Any]] = []
        for row in rows:
            chat_id = int(row[0])
            title = str(row[2] or chat_id)
            result.append(
                {
                    "chat_id": chat_id,
                    "title": title[:60],
                    "last_ts": str(row[1] or ""),
                }
            )
        return result
