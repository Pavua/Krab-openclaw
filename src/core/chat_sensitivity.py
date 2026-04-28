# -*- coding: utf-8 -*-
"""
Реестр sensitive чатов (Idea 28).

Зачем:

Часть чатов (financial, work, family) не должна попадать в `archive.db` /
`vec_chunks` memory layer. Сообщения из таких чатов либо вообще не индексируются
(`level='no_archive'`), либо проходят PII-redaction перед индексацией
(`level='redact_only'`).

Хранилище — JSON файл `~/.openclaw/krab_runtime_state/sensitive_chats.json`,
лениво загружается на первый read, persist'ится после каждого write. Pattern
совпадает с `chat_ban_cache.py` / `silence_mode.py` (тот же RLock + lazy load +
configure_default_path).

### Уровни (level)

- ``no_archive`` — пропуск в memory layer полностью; сообщение не доходит до
  archive.add_message и не embed'ится. Используется для денежных/документных
  чатов, где даже факт обсуждения нежелателен в long-term memory.
- ``redact_only`` — сообщение проходит PII-redaction (`pii_redactor.PIIRedactor`)
  и потом уходит в archive. Подходит для рабочих чатов, где смысл сохранять, но
  телефоны/токены/CC лучше вычистить.

### Не решает
- Не делает retroactive cleanup уже проиндексированных сообщений (это отдельная
  процедура memory_doctor).
- Не интегрирован в memory layer — wire-up в `archive.add_message` / индексаторе
  делается отдельным шагом (см. README backlog).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from .logger import get_logger

logger = get_logger(__name__)


SensitivityLevel = Literal["no_archive", "redact_only"]

_VALID_LEVELS: frozenset[str] = frozenset({"no_archive", "redact_only"})


class SensitiveChatRegistry:
    """Потокобезопасный registry sensitive чатов с persist в JSON.

    Module-level singleton — `sensitive_chat_registry`. В рантайме инициализируется
    через `configure_default_path()` из bootstrap. В тестах — `storage_path` в
    конструкторе.
    """

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._entries: dict[str, dict[str, Any]] = {}
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает данные с диска."""
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def is_sensitive(self, chat_id: Any) -> bool:
        """True → чат помечен как sensitive (любой level)."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            return target in self._entries

    def get_level(self, chat_id: Any) -> SensitivityLevel | None:
        """Возвращает level для чата либо None если чат не помечен."""
        target = self._normalize(chat_id)
        if not target:
            return None
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return None
            level = entry.get("level")
            if level in _VALID_LEVELS:
                return level  # type: ignore[return-value]
            return None

    def mark_sensitive(
        self,
        chat_id: Any,
        reason: str,
        level: SensitivityLevel = "no_archive",
    ) -> None:
        """Помечает чат как sensitive с указанным level и причиной."""
        target = self._normalize(chat_id)
        if not target:
            return
        if level not in _VALID_LEVELS:
            raise ValueError(
                f"invalid sensitivity level: {level!r} (allowed: {sorted(_VALID_LEVELS)})"
            )
        normalized_reason = (reason or "").strip() or "unspecified"
        with self._lock:
            self._entries[target] = {
                "level": level,
                "reason": normalized_reason,
            }
            self._persist_to_disk()
        logger.info(
            "sensitive_chat_marked",
            chat_id=target,
            level=level,
            reason=normalized_reason,
        )

    def unmark(self, chat_id: Any) -> bool:
        """Удаляет метку sensitive. True если запись была."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._entries:
                return False
            del self._entries[target]
            self._persist_to_disk()
        logger.info("sensitive_chat_unmarked", chat_id=target)
        return True

    def list_entries(self) -> list[dict[str, Any]]:
        """Снимок текущих записей (копии, безопасные для caller'а)."""
        with self._lock:
            result: list[dict[str, Any]] = []
            for chat_id, entry in self._entries.items():
                snapshot = dict(entry)
                snapshot["chat_id"] = chat_id
                result.append(snapshot)
            return result

    def should_skip_archive(self, chat_id: Any) -> bool:
        """Удобный helper для memory layer: True → не пиши в archive.db вообще."""
        return self.get_level(chat_id) == "no_archive"

    def should_redact(self, chat_id: Any) -> bool:
        """Удобный helper: True → пропусти текст через PII-redactor перед save."""
        return self.get_level(chat_id) == "redact_only"

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _normalize(chat_id: Any) -> str:
        return str(chat_id or "").strip()

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "sensitive_chat_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("sensitive_chat_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            level = value.get("level")
            if level not in _VALID_LEVELS:
                skipped += 1
                continue
            self._entries[str(key)] = {
                "level": level,
                "reason": str(value.get("reason") or "unspecified"),
            }
            loaded += 1
        if loaded or skipped:
            logger.info("sensitive_chat_loaded", loaded=loaded, skipped=skipped)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "sensitive_chat_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


def valid_levels() -> Iterable[str]:
    """Возвращает допустимые значения level (для UI / валидации)."""
    return tuple(sorted(_VALID_LEVELS))


# Module-level singleton — pattern как в chat_ban_cache / silence_mode.
sensitive_chat_registry = SensitiveChatRegistry()
