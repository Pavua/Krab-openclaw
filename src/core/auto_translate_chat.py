# -*- coding: utf-8 -*-
"""
Per-chat реестр настроек авто-перевода (Idea 4).

### Зачем

Если в чате обычно общаются на неродном для оператора языке (например, испанская
группа в Telegram где владелец читает на русском), удобно автоматически
прикладывать inline-перевод к incoming/outgoing сообщениям без явного
`!translate`. Этот модуль — *чистый config + format API*: он только хранит
language-pair конфигурацию для каждого чата и форматирует уже переведённую
строку. Он **НЕ вызывает** translator engine, чтобы не дублировать существующую
бизнес-логику в `src/core/translator_*` и не создавать циклические зависимости.

Active translator pipeline (вызов engine, кеширование переводов, гейты на
finish/preflight) живёт в существующих модулях; здесь — лишь registry и
helper для format. Интеграция произойдёт в userbot/handlers отдельно.

### Хранение

Persisted JSON: `~/.openclaw/krab_runtime_state/auto_translate_chats.json`.
Структура:

```json
{
    "-1001234": {
        "source_lang": "es",
        "target_lang": "ru",
        "direction": "inline",
        "registered_at": "2026-04-29T10:00:00+00:00"
    }
}
```

### Direction

- `inline` — перевод приклеивается к исходному тексту в том же сообщении.
- `reply` — отдельным reply-сообщением (caller сам решает как доставлять).
- `both`  — и в исходное сообщение, и отдельным reply.

Сам `format_inline_translation()` обслуживает только inline-составляющую;
caller отвечает за reply-доставку, исходя из `direction`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .logger import get_logger

logger = get_logger(__name__)

Direction = Literal["inline", "reply", "both"]
_VALID_DIRECTIONS: frozenset[str] = frozenset({"inline", "reply", "both"})


class ChatTranslateConfig:
    """Реестр per-chat настроек авто-перевода с persist на диск.

    Используется как module-level singleton (`auto_translate_chats`). В рантайме
    путь конфигурируется через `configure_default_path()` из bootstrap.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._entries: dict[str, dict[str, Any]] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает то, что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def register_chat(
        self,
        chat_id: Any,
        source: str,
        target: str,
        direction: Direction = "inline",
    ) -> None:
        """Регистрирует чат как авто-переводимый.

        `source` / `target` — ISO-639-1 коды (`es`, `ru`, `en` ...).
        Повторный register для того же чата перезаписывает конфиг.
        """
        key = self._normalize(chat_id)
        if not key:
            return
        src = (source or "").strip().lower()
        tgt = (target or "").strip().lower()
        if not src or not tgt:
            raise ValueError("source/target language codes must be non-empty")
        if src == tgt:
            raise ValueError("source and target languages must differ")
        if direction not in _VALID_DIRECTIONS:
            raise ValueError(f"invalid direction: {direction!r}")
        with self._lock:
            self._entries[key] = {
                "source_lang": src,
                "target_lang": tgt,
                "direction": direction,
                "registered_at": self._now_fn().isoformat(),
            }
            self._persist_to_disk()
        logger.info(
            "auto_translate_chat_registered",
            chat_id=key,
            source=src,
            target=tgt,
            direction=direction,
        )

    def unregister_chat(self, chat_id: Any) -> bool:
        """Удаляет конфиг чата. Возвращает True если запись была."""
        key = self._normalize(chat_id)
        if not key:
            return False
        with self._lock:
            if key not in self._entries:
                return False
            del self._entries[key]
            self._persist_to_disk()
        logger.info("auto_translate_chat_unregistered", chat_id=key)
        return True

    def get_config(self, chat_id: Any) -> dict[str, Any] | None:
        """Возвращает копию конфига или None, если чат не зарегистрирован."""
        key = self._normalize(chat_id)
        if not key:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            return dict(entry)

    def should_translate(self, chat_id: Any, detected_lang: str | None) -> bool:
        """True → нужно автоматически переводить сообщение в этом чате.

        Логика: чат должен быть зарегистрирован, и detected_lang должен совпасть
        с `source_lang`. Если detected_lang неизвестен (None / пусто) — возвращаем
        False: caller должен сам решить, делать ли fallback (например, через
        дополнительное определение языка).
        """
        key = self._normalize(chat_id)
        if not key:
            return False
        normalized = (detected_lang or "").strip().lower()
        if not normalized:
            return False
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            return str(entry.get("source_lang") or "").lower() == normalized

    @staticmethod
    def format_inline_translation(original: str, translated: str) -> str:
        """Форматирует inline-перевод вида `Привет [Hello]`.

        Если translated пустой/равен original → возвращает original без брэкетов.
        Это безопасный fallback: не загромождаем чат повторами или пустыми скобками,
        если translator engine выдал нулевой результат.
        """
        orig = original or ""
        trans = (translated or "").strip()
        if not trans or trans == orig.strip():
            return orig
        return f"{orig} [{trans}]"

    def list_chats(self) -> list[dict[str, Any]]:
        """Снимок зарегистрированных чатов для owner UI / диагностики."""
        with self._lock:
            result: list[dict[str, Any]] = []
            for chat_id, entry in self._entries.items():
                snapshot = dict(entry)
                snapshot["chat_id"] = chat_id
                result.append(snapshot)
            return result

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
                "auto_translate_chat_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("auto_translate_chat_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            direction = value.get("direction", "inline")
            if direction not in _VALID_DIRECTIONS:
                skipped += 1
                continue
            self._entries[str(key)] = dict(value)
            loaded += 1
        if loaded or skipped:
            logger.info(
                "auto_translate_chat_loaded",
                loaded=loaded,
                skipped=skipped,
            )

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
                "auto_translate_chat_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton (pattern совпадает с chat_ban_cache, silence_manager,
# inbox_service). Путь конфигурируется через configure_default_path() из bootstrap.
auto_translate_chats = ChatTranslateConfig()
