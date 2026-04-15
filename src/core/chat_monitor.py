# -*- coding: utf-8 -*-
"""
Сервис мониторинга чатов — уникальная фича юзербота.
Юзербот видит ВСЕ входящие сообщения, поэтому можно отслеживать
любой чат на ключевые слова/regex без добавления бота в группу.

Конфигурация хранится в ~/.openclaw/krab_runtime_state/chat_monitors.json
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from .logger import get_logger

logger = get_logger(__name__)

# Путь к файлу конфигурации мониторингов
_DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
_MONITORS_FILE = _DEFAULT_STATE_DIR / "chat_monitors.json"


class MonitorEntry:
    """Запись об одном мониторинге чата."""

    def __init__(
        self,
        chat_id: int | str,
        chat_title: str,
        keywords: list[str],
        added_at: float | None = None,
    ) -> None:
        # chat_id храним как str для JSON-сериализации
        self.chat_id: str = str(chat_id)
        self.chat_title: str = chat_title
        # keywords могут быть обычными строками или regex-паттернами (начинаются с "re:")
        self.keywords: list[str] = keywords
        self.added_at: float = added_at or time.time()
        # Скомпилированные паттерны (не сериализуются, пересобираются при загрузке)
        # Список пар (keyword, compiled_pattern); невалидные regex пропускаются
        self._pairs: list[tuple[str, re.Pattern[str]]] = self._compile_patterns()

    def _compile_patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        """Компилируем keyword-паттерны: re:<pattern> → regex, иначе plain match.
        Возвращает список пар (keyword, pattern) — невалидные regex пропускаются."""
        pairs = []
        for kw in self.keywords:
            if kw.startswith("re:"):
                # Явный regex-паттерн
                raw = kw[3:]
                try:
                    pairs.append((kw, re.compile(raw, re.IGNORECASE)))
                except re.error as e:
                    logger.warning("monitor_bad_regex", pattern=raw, error=str(e))
            else:
                # Обычная строка — ищем как подстроку без учёта регистра
                pairs.append((kw, re.compile(re.escape(kw), re.IGNORECASE)))
        return pairs

    def match(self, text: str) -> str | None:
        """Возвращает первое совпавшее ключевое слово или None."""
        for kw, pattern in self._pairs:
            if pattern.search(text):
                return kw
        return None

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "chat_title": self.chat_title,
            "keywords": self.keywords,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MonitorEntry":
        return cls(
            chat_id=data["chat_id"],
            chat_title=data.get("chat_title", str(data["chat_id"])),
            keywords=data.get("keywords", []),
            added_at=data.get("added_at"),
        )


class ChatMonitorService:
    """
    Сервис мониторинга чатов на ключевые слова.

    Использование:
        chat_monitor.add(chat_id, chat_title, keywords)
        chat_monitor.remove(chat_id)
        matched_kw = chat_monitor.check_message(chat_id, text)
    """

    def __init__(self, state_file: Path | None = None) -> None:
        self._state_file = state_file or _MONITORS_FILE
        # Словарь chat_id(str) → MonitorEntry
        self._monitors: dict[str, MonitorEntry] = {}
        self._load()

    # ──────────────────────────────────────────────
    # Персистентность
    # ──────────────────────────────────────────────

    def _load(self) -> None:
        """Загружаем конфигурацию из файла при старте."""
        try:
            if self._state_file.exists():
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                for item in raw.get("monitors", []):
                    entry = MonitorEntry.from_dict(item)
                    self._monitors[entry.chat_id] = entry
                logger.info("chat_monitors_loaded", count=len(self._monitors))
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("chat_monitors_load_error", error=str(e))

    def _save(self) -> None:
        """Сохраняем текущую конфигурацию в файл."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"monitors": [e.to_dict() for e in self._monitors.values()]}
            self._state_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.error("chat_monitors_save_error", error=str(e))

    # ──────────────────────────────────────────────
    # Публичный API
    # ──────────────────────────────────────────────

    def add(
        self,
        chat_id: int | str,
        chat_title: str,
        keywords: list[str],
    ) -> MonitorEntry:
        """Добавить или обновить мониторинг чата."""
        entry = MonitorEntry(chat_id=chat_id, chat_title=chat_title, keywords=keywords)
        self._monitors[entry.chat_id] = entry
        self._save()
        logger.info("monitor_added", chat_id=entry.chat_id, keywords=keywords)
        return entry

    def remove(self, chat_id: int | str) -> bool:
        """Удалить мониторинг. Возвращает True если был активен."""
        key = str(chat_id)
        if key in self._monitors:
            del self._monitors[key]
            self._save()
            logger.info("monitor_removed", chat_id=key)
            return True
        return False

    def list_monitors(self) -> list[MonitorEntry]:
        """Список всех активных мониторингов."""
        return list(self._monitors.values())

    def check_message(self, chat_id: int | str, text: str) -> Optional[str]:
        """
        Проверяет текст сообщения из chat_id по активным мониторингам.
        Возвращает первое совпавшее ключевое слово или None.
        """
        if not text:
            return None
        key = str(chat_id)
        entry = self._monitors.get(key)
        if entry is None:
            return None
        return entry.match(text)

    def get_entry(self, chat_id: int | str) -> Optional[MonitorEntry]:
        """Вернуть MonitorEntry по chat_id или None."""
        return self._monitors.get(str(chat_id))

    @property
    def active_chat_ids(self) -> set[str]:
        """Множество chat_id с активным мониторингом."""
        return set(self._monitors.keys())


# Синглтон — используется везде в проекте
chat_monitor_service = ChatMonitorService()
