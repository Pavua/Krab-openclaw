# -*- coding: utf-8 -*-
"""
PersonalTodoService — персональный менеджер задач прямо из Telegram.

Хранилище: ~/.openclaw/krab_runtime_state/personal_todos.json
Каждая задача: {id, text, done, created_at}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TypedDict

from .logger import get_logger

logger = get_logger(__name__)

# Путь к хранилищу задач
TODO_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "personal_todos.json"


class TodoItem(TypedDict):
    """Структура одной задачи."""

    id: int
    text: str
    done: bool
    created_at: float


class PersonalTodoService:
    """Сервис управления персональными задачами."""

    def __init__(self, todo_file: Path | None = None) -> None:
        # Позволяем переопределить путь в тестах
        self.todo_file = todo_file or TODO_FILE

    # ------------------------------------------------------------------
    # Внутренние методы
    # ------------------------------------------------------------------

    def _load(self) -> list[TodoItem]:
        """Загружает список задач из файла. При ошибке возвращает []."""
        if not self.todo_file.exists():
            return []
        try:
            data = json.loads(self.todo_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data  # type: ignore[return-value]
        except Exception as exc:
            logger.error("Ошибка загрузки todo-файла: %s", exc)
        return []

    def _save(self, items: list[TodoItem]) -> None:
        """Сохраняет список задач в файл."""
        try:
            self.todo_file.parent.mkdir(parents=True, exist_ok=True)
            self.todo_file.write_text(
                json.dumps(items, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("Ошибка сохранения todo-файла: %s", exc)

    def _next_id(self, items: list[TodoItem]) -> int:
        """Генерирует следующий числовой ID (монотонный, не переиспользуется)."""
        return max((t["id"] for t in items), default=0) + 1

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def add(self, text: str) -> TodoItem:
        """Добавляет новую задачу и возвращает её."""
        items = self._load()
        item: TodoItem = {
            "id": self._next_id(items),
            "text": text.strip(),
            "done": False,
            "created_at": time.time(),
        }
        items.append(item)
        self._save(items)
        logger.debug("todo add id=%d text=%r", item["id"], item["text"])
        return item

    def list_all(self) -> list[TodoItem]:
        """Возвращает все задачи (активные первыми, выполненные в конце)."""
        items = self._load()
        # Активные → выполненные, внутри каждой группы по id
        return sorted(items, key=lambda t: (t["done"], t["id"]))

    def mark_done(self, todo_id: int) -> TodoItem | None:
        """Отмечает задачу выполненной. Возвращает None если не найдена."""
        items = self._load()
        for item in items:
            if item["id"] == todo_id:
                item["done"] = True
                self._save(items)
                return item
        return None

    def delete(self, todo_id: int) -> bool:
        """Удаляет задачу. Возвращает True если удалена."""
        items = self._load()
        new_items = [t for t in items if t["id"] != todo_id]
        if len(new_items) == len(items):
            return False
        self._save(new_items)
        return True

    def clear_done(self) -> int:
        """Удаляет все выполненные задачи. Возвращает количество удалённых."""
        items = self._load()
        active = [t for t in items if not t["done"]]
        removed = len(items) - len(active)
        if removed:
            self._save(active)
        return removed

    def render(self) -> str:
        """Форматирует список задач для Telegram-ответа."""
        items = self.list_all()
        if not items:
            return "📋 Список задач пуст. Добавь: `!todo add <текст>`"

        active_count = sum(1 for t in items if not t["done"])
        done_count = sum(1 for t in items if t["done"])

        # Заголовок
        parts = ["📋 **Задачи**"]
        counts: list[str] = []
        if active_count:
            counts.append(f"{active_count} активных")
        if done_count:
            counts.append(f"{done_count} выполнено")
        if counts:
            parts[0] += f" ({', '.join(counts)})"
        parts.append("─────────────")

        for item in items:
            if item["done"]:
                parts.append(f"✅ {item['id']}. ~{item['text']}~")
            else:
                parts.append(f"⬜ {item['id']}. {item['text']}")

        return "\n".join(parts)


# Синглтон для использования в production
personal_todo_service = PersonalTodoService()
