# -*- coding: utf-8 -*-
"""
MemoService — быстрые заметки из Telegram в Obsidian vault.

Сохраняет заметки в ~/Documents/Obsidian Vault/00_Inbox/ с frontmatter YAML.
Поддерживает: сохранение, список последних, поиск по тексту.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from .logger import get_logger

logger = get_logger(__name__)

# Путь к Obsidian vault и папке входящих
OBSIDIAN_VAULT = Path("/Users/pablito/Documents/Obsidian Vault")
OBSIDIAN_INBOX = OBSIDIAN_VAULT / "00_Inbox"


class MemoResult(NamedTuple):
    """Результат операции с заметкой."""
    success: bool
    message: str
    file_path: Path | None = None


class MemoService:
    """Сервис управления быстрыми заметками в Obsidian."""

    def __init__(self, inbox_dir: Path | None = None) -> None:
        # Позволяем переопределить путь в тестах
        self.inbox_dir = inbox_dir or OBSIDIAN_INBOX

    def _ensure_inbox(self) -> bool:
        """Создаёт папку inbox, если её нет. Возвращает True при успехе."""
        try:
            self.inbox_dir.mkdir(parents=True, exist_ok=True)
            return True
        except OSError as e:
            logger.error("Не удалось создать папку inbox: %s", e)
            return False

    def _build_filename(self, dt: datetime) -> str:
        """
        Формирует уникальное имя файла заметки.

        Базовый формат: YYYY-MM-DD_HH-MM_memo.md
        При коллизии добавляем секунды, затем числовой суффикс.
        """
        base = dt.strftime("%Y-%m-%d_%H-%M_memo")
        candidate = self.inbox_dir / f"{base}.md"
        if not candidate.exists():
            return f"{base}.md"

        # Пробуем с секундами
        base = dt.strftime("%Y-%m-%d_%H-%M-%S_memo")
        candidate = self.inbox_dir / f"{base}.md"
        if not candidate.exists():
            return f"{base}.md"

        # Числовой суффикс до уникальности
        for n in range(2, 10000):
            name = f"{base}_{n}.md"
            if not (self.inbox_dir / name).exists():
                return name

        # Крайний случай — timestamp в микросекундах
        return f"{dt.strftime('%Y-%m-%d_%H-%M-%S-%f')}_memo.md"

    def _build_content(
        self,
        text: str,
        chat_title: str,
        dt: datetime,
        tags: list[str] | None = None,
        source_type: str = "krab-telegram",
    ) -> str:
        """Строит содержимое файла с YAML frontmatter."""
        created = dt.strftime("%Y-%m-%dT%H:%M:%S")
        # Теги в YAML-списке
        tags_yaml = ""
        if tags:
            safe_tags = [t.replace('"', '') for t in tags]
            tags_yaml = "tags:\n" + "".join(f'  - "{t}"\n' for t in safe_tags)
        return (
            "---\n"
            f"created: {created}\n"
            f"source: {source_type}\n"
            f"chat: {chat_title}\n"
            f"{tags_yaml}"
            "---\n"
            "\n"
            f"{text}\n"
        )

    def save(
        self,
        text: str,
        chat_title: str = "unknown",
        tags: list[str] | None = None,
        source_type: str = "krab-telegram",
    ) -> MemoResult:
        """
        Сохраняет заметку в inbox.

        Args:
            text: Текст заметки.
            chat_title: Название чата-источника.
            tags: Список тегов для YAML frontmatter.
            source_type: Тип источника (krab-telegram, krab-voice и т.д.).

        Returns:
            MemoResult с результатом операции.
        """
        if not text.strip():
            return MemoResult(success=False, message="Текст заметки не может быть пустым.")

        if not self._ensure_inbox():
            return MemoResult(
                success=False,
                message=f"Папка inbox недоступна: {self.inbox_dir}",
            )

        dt = datetime.now()
        filename = self._build_filename(dt)
        file_path = self.inbox_dir / filename
        content = self._build_content(text.strip(), chat_title, dt, tags=tags, source_type=source_type)

        try:
            file_path.write_text(content, encoding="utf-8")
            logger.info("Заметка сохранена: %s", file_path)
            return MemoResult(
                success=True,
                message=f"Заметка сохранена: `{filename}`",
                file_path=file_path,
            )
        except OSError as e:
            logger.error("Ошибка записи заметки: %s", e)
            return MemoResult(success=False, message=f"Ошибка записи: {e}")

    def list_recent(self, n: int = 5) -> list[dict]:
        """
        Возвращает список последних N заметок из inbox.

        Returns:
            Список словарей с ключами: filename, created, preview.
        """
        if not self.inbox_dir.exists():
            return []

        # Берём только memo-файлы по паттерну (включая варианты с суффиксами)
        memo_files = sorted(
            self.inbox_dir.glob("????-??-??_??-??*_memo*.md"),
            key=lambda p: p.name,
            reverse=True,
        )[:n]

        result = []
        for path in memo_files:
            try:
                content = path.read_text(encoding="utf-8")
                # Извлекаем created из frontmatter
                created_match = re.search(r"^created:\s*(.+)$", content, re.MULTILINE)
                created = created_match.group(1).strip() if created_match else "—"
                # Превью: первая непустая строка после frontmatter
                body = re.sub(r"^---\n.*?^---\n", "", content, flags=re.DOTALL | re.MULTILINE)
                preview = next(
                    (line.strip() for line in body.splitlines() if line.strip()), "—"
                )
                if len(preview) > 80:
                    preview = preview[:77] + "..."
                result.append({
                    "filename": path.name,
                    "created": created,
                    "preview": preview,
                })
            except OSError:
                continue

        return result

    def search(self, query: str) -> list[dict]:
        """
        Поиск по заметкам (case-insensitive grep по содержимому).

        Returns:
            Список словарей с ключами: filename, line, match.
        """
        if not self.inbox_dir.exists():
            return []

        query_lower = query.lower()
        results = []

        for path in sorted(self.inbox_dir.glob("????-??-??_??-??*_memo*.md")):
            try:
                content = path.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if query_lower in line.lower():
                        preview = line.strip()
                        if len(preview) > 100:
                            preview = preview[:97] + "..."
                        results.append({
                            "filename": path.name,
                            "match": preview,
                        })
                        # Одно совпадение на файл для краткости
                        break
            except OSError:
                continue

        return results

    async def save_async(
        self,
        text: str,
        chat_title: str = "unknown",
        tags: list[str] | None = None,
        source_type: str = "krab-telegram",
    ) -> MemoResult:
        """Асинхронная обёртка над save() для использования в async-хендлерах."""
        import functools

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self.save, text, chat_title, tags=tags, source_type=source_type),
        )


# Синглтон
memo_service = MemoService()
