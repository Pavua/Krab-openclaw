# -*- coding: utf-8 -*-
"""
BookmarkService — закладки на важные Telegram-сообщения.

Хранит закладки в ~/.openclaw/krab_runtime_state/bookmarks.json.
Каждая закладка: {id, chat_id, chat_title, message_id, text_preview, from_user, timestamp}
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Путь к файлу хранения закладок
_DEFAULT_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "bookmarks.json"

# Максимальная длина превью текста
_PREVIEW_LEN = 200


class BookmarkService:
    """Сервис закладок на сообщения Telegram."""

    def __init__(self, store_path: Path | None = None) -> None:
        # Позволяем переопределить путь в тестах
        self._path = store_path or _DEFAULT_PATH
        self._bookmarks: list[dict[str, Any]] | None = None

    # ─── Загрузка / Сохранение ────────────────────────────────────────────────

    def _load(self) -> list[dict[str, Any]]:
        """Загружает закладки из файла (ленивая инициализация)."""
        if self._bookmarks is not None:
            return self._bookmarks
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._bookmarks = data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("bookmarks.json повреждён, сбрасываем: %s", exc)
                self._bookmarks = []
        else:
            self._bookmarks = []
        return self._bookmarks

    def _save(self) -> None:
        """Записывает текущее состояние закладок на диск."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._bookmarks, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Ошибка записи bookmarks.json: %s", exc)

    def _next_id(self) -> int:
        """Генерирует следующий числовой ID."""
        bookmarks = self._load()
        if not bookmarks:
            return 1
        return max(b["id"] for b in bookmarks) + 1

    # ─── Публичный API ────────────────────────────────────────────────────────

    def add(
        self,
        *,
        chat_id: int,
        chat_title: str,
        message_id: int,
        text: str,
        from_user: str,
    ) -> dict[str, Any]:
        """
        Добавляет закладку.

        Returns:
            Созданная закладка (словарь).
        """
        bookmarks = self._load()

        # Проверяем дубликаты: тот же чат + то же сообщение
        for b in bookmarks:
            if b["chat_id"] == chat_id and b["message_id"] == message_id:
                return b  # уже сохранено, возвращаем существующую

        preview = text.strip()
        if len(preview) > _PREVIEW_LEN:
            preview = preview[: _PREVIEW_LEN - 1] + "…"

        bookmark: dict[str, Any] = {
            "id": self._next_id(),
            "chat_id": chat_id,
            "chat_title": chat_title,
            "message_id": message_id,
            "text_preview": preview,
            "from_user": from_user,
            "timestamp": time.time(),
        }
        bookmarks.append(bookmark)
        self._save()
        logger.info("Закладка #%d добавлена (chat=%s msg=%d)", bookmark["id"], chat_id, message_id)
        return bookmark

    def list_all(self) -> list[dict[str, Any]]:
        """Возвращает все закладки (от новых к старым)."""
        bookmarks = self._load()
        return sorted(bookmarks, key=lambda b: b["timestamp"], reverse=True)

    def search(self, query: str) -> list[dict[str, Any]]:
        """
        Поиск по полям text_preview, chat_title, from_user (case-insensitive).

        Returns:
            Список подходящих закладок (от новых к старым).
        """
        q = query.lower()
        result = [
            b
            for b in self._load()
            if (
                q in b.get("text_preview", "").lower()
                or q in b.get("chat_title", "").lower()
                or q in b.get("from_user", "").lower()
            )
        ]
        return sorted(result, key=lambda b: b["timestamp"], reverse=True)

    def delete(self, bookmark_id: int) -> bool:
        """
        Удаляет закладку по ID.

        Returns:
            True если удалено, False если не найдено.
        """
        bookmarks = self._load()
        before = len(bookmarks)
        self._bookmarks = [b for b in bookmarks if b["id"] != bookmark_id]
        if len(self._bookmarks) < before:
            self._save()
            logger.info("Закладка #%d удалена", bookmark_id)
            return True
        return False

    def get(self, bookmark_id: int) -> dict[str, Any] | None:
        """Возвращает закладку по ID или None."""
        for b in self._load():
            if b["id"] == bookmark_id:
                return b
        return None

    # ─── Async-обёртки ────────────────────────────────────────────────────────

    async def add_async(self, **kwargs: Any) -> dict[str, Any]:
        """Асинхронная обёртка над add()."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.add(**kwargs))

    async def delete_async(self, bookmark_id: int) -> bool:
        """Асинхронная обёртка над delete()."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.delete, bookmark_id)


# Синглтон
bookmark_service = BookmarkService()
