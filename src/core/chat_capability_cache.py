# -*- coding: utf-8 -*-
"""
Persisted cache per-chat capabilities (Telegram chat permissions).

Зачем это нужно:

До B.6 Краб не знал ничего о настройках чата в который он пишет:
- есть ли slow mode (минимальный интервал между сообщениями);
- разрешены ли голосовые сообщения в принципе.
Поэтому в чатах с slow_mode 10s (как How2AI) Краб мог попытаться
отправить TTS сразу после текстового ответа → `FloodWait` → retry →
SpamBot автоматически поднимает aggressiveness score аккаунта. То же
самое с voice-отключёнными чатами: попытка send_voice → отказ → лог
error + wasted attempt.

Решение (B.6): один раз в ~24 часа для каждого чата делаем
`client.get_chat(chat_id)` и запоминаем **три минимальных поля**:
- `slow_mode_seconds` (int | None),
- `voice_allowed` (bool | None),
- `text_allowed` (bool | None).
Дальше при любых decision'ах (где-нибудь в `_run_llm_request_flow`)
мы читаем cache без лишних API вызовов.

### Почему TTL а не no-expiry
Админы меняют chat permissions руками, slow mode включают/выключают
в течение часа. 24h TTL — компромисс: достаточно редко чтобы не
грузить API, достаточно свежо чтобы не зависнуть на устаревшей
информации несколько дней.

### Почему persist а не in-memory
Каждый рестарт Краба не должен заново дёргать `get_chat` для каждого
чата (это 50+ API-вызовов только при старте, что само по себе может
триггернуть FloodWait). JSON на диске переживает рестарт и
перечитывается лениво.

### Что НЕ делает
- Не делает `get_chat` превентивно для всех известных чатов. Ленивое
  fetching: первый запрос к чату → get_chat → cache. Дальше — hot path.
- Не подписывается на `ChatPermissionsUpdate` события (они не всегда
  приходят userbot'у). Полагается на TTL для обновления.
- Не отвечает за voice fallback сам — только хранит state. Решение о
  skip voice принимается на call-site (userbot_bridge).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Default TTL for cache entries: 24 hours. Конфигурируется через
# `config.CHAT_CAPABILITY_CACHE_TTL_HOURS`, но дефолт держим в модуле
# чтобы тесты работали без config.
_DEFAULT_TTL_HOURS: float = 24.0


class ChatCapabilityCache:
    """
    Thread-safe cache of per-chat capabilities with JSON persistence.

    Используется как module-level singleton (`chat_capability_cache`). Инжектится
    в тестах через `storage_path` и через fake `fetcher` (см. `upsert_from_fetch`).
    """

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._entries: dict[str, dict[str, Any]] = {}
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """
        Устанавливает путь к persisted JSON и подгружает то что лежит на диске.

        Вызывается из `KraabUserbot.start()` при bootstrap.
        """
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Hot path read --------------------------------------------------

    def get(self, chat_id: Any, *, ttl_hours: float = _DEFAULT_TTL_HOURS) -> dict[str, Any] | None:
        """
        Возвращает cached capability entry для чата или None если нет / истёк.

        None → caller должен сделать fetch через `upsert_from_fetch`. Ленивое
        expiry: если запись протухла, она вычищается из памяти (но не persist'ится
        на read — persist происходит только на upsert/invalidate).
        """
        target = self._normalize(chat_id)
        if not target:
            return None
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return None
            fetched_at_str = str(entry.get("fetched_at") or "")
            try:
                fetched_at = datetime.fromisoformat(fetched_at_str)
            except (TypeError, ValueError):
                del self._entries[target]
                return None
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - fetched_at
            if age > timedelta(hours=ttl_hours):
                del self._entries[target]
                return None
            return dict(entry)

    def is_voice_allowed(
        self, chat_id: Any, *, ttl_hours: float = _DEFAULT_TTL_HOURS
    ) -> bool | None:
        """
        Convenience: возвращает True / False если cache знает, None если запись
        отсутствует или истекла.

        Семантика `True/False/None` намеренная: caller'у важно отличать «точно
        нельзя» от «не знаю», потому что в случае «не знаю» default — разрешать
        (back-compat с до-B.6 поведением).
        """
        entry = self.get(chat_id, ttl_hours=ttl_hours)
        if entry is None:
            return None
        value = entry.get("voice_allowed")
        if value is None:
            return None
        return bool(value)

    def get_slow_mode_seconds(
        self, chat_id: Any, *, ttl_hours: float = _DEFAULT_TTL_HOURS
    ) -> int | None:
        """Возвращает slow_mode_seconds из cache или None если нет записи."""
        entry = self.get(chat_id, ttl_hours=ttl_hours)
        if entry is None:
            return None
        value = entry.get("slow_mode_seconds")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    # ---- Write path -----------------------------------------------------

    def upsert(
        self,
        chat_id: Any,
        *,
        slow_mode_seconds: int | None,
        voice_allowed: bool | None,
        text_allowed: bool | None,
        chat_type: str | None = None,
    ) -> dict[str, Any]:
        """
        Записывает capability entry в cache и persist'ит на диск.

        Это низкоуровневый write API. Обычно caller использует
        `upsert_from_chat(...)` который принимает pyrogram Chat object.
        """
        target = self._normalize(chat_id)
        if not target:
            raise ValueError("chat_id required")
        now = datetime.now(timezone.utc)
        entry = {
            "chat_id": target,
            "chat_type": str(chat_type or "unknown"),
            "slow_mode_seconds": int(slow_mode_seconds) if slow_mode_seconds is not None else None,
            "voice_allowed": bool(voice_allowed) if voice_allowed is not None else None,
            "text_allowed": bool(text_allowed) if text_allowed is not None else None,
            "fetched_at": now.isoformat(),
        }
        with self._lock:
            self._entries[target] = entry
            self._persist_to_disk()
        logger.info(
            "chat_capability_cache_upserted",
            chat_id=target,
            slow_mode_seconds=entry["slow_mode_seconds"],
            voice_allowed=entry["voice_allowed"],
            text_allowed=entry["text_allowed"],
        )
        return dict(entry)

    def upsert_from_chat(self, chat: Any) -> dict[str, Any]:
        """
        Читает capability поля из pyrogram Chat object и upsert'ит.

        Вызывается caller'ом после успешного `await client.get_chat(chat_id)`.
        Вынесено сюда, чтобы форматирование было консистентным между
        production fetch (`userbot_bridge`) и тестами (которые могут
        подсунуть fake pyrogram Chat через SimpleNamespace).
        """
        chat_id = getattr(chat, "id", None)
        chat_type = getattr(chat, "type", None)
        chat_type_name = getattr(chat_type, "name", None) or str(chat_type or "")

        slow_mode_seconds = getattr(chat, "slow_mode_delay", None)
        permissions = getattr(chat, "permissions", None)

        # Default to None ("unknown") вместо True чтобы call-sites могли
        # отличать «Telegram точно сказал разрешено» от «поле отсутствовало».
        voice_allowed: bool | None = None
        text_allowed: bool | None = None
        if permissions is not None:
            # ChatPermissions.can_send_voices — специфичный voice flag.
            # ChatPermissions.can_send_media_messages — родительский media flag
            # (подразумевает voices). В pyrofork оба могут быть None если
            # никогда явно не выставлялись админом, тогда и мы держим None.
            can_voices = getattr(permissions, "can_send_voices", None)
            can_media = getattr(permissions, "can_send_media_messages", None)
            if can_voices is not None:
                voice_allowed = bool(can_voices)
            elif can_media is not None:
                # Consider media permission a proxy when voice-specific
                # flag is missing — if media is disallowed, voices are too.
                voice_allowed = bool(can_media)

            can_text = getattr(permissions, "can_send_messages", None)
            if can_text is not None:
                text_allowed = bool(can_text)

        return self.upsert(
            chat_id=chat_id,
            slow_mode_seconds=slow_mode_seconds,
            voice_allowed=voice_allowed,
            text_allowed=text_allowed,
            chat_type=chat_type_name or None,
        )

    def invalidate(self, chat_id: Any) -> bool:
        """Удаляет запись (owner override / debug). True если была запись."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._entries:
                return False
            del self._entries[target]
            self._persist_to_disk()
        logger.info("chat_capability_cache_invalidated", chat_id=target)
        return True

    def list_entries(self, *, ttl_hours: float = _DEFAULT_TTL_HOURS) -> list[dict[str, Any]]:
        """
        Возвращает копии всех активных (не истёкших) entry для owner UI.

        Ленивый expiry тоже применяется: устаревшие записи вычищаются при
        обходе, так что `!chatcap status` не показывает старые данные.
        """
        now = datetime.now(timezone.utc)
        result: list[dict[str, Any]] = []
        with self._lock:
            for chat_id in list(self._entries.keys()):
                entry = self._entries[chat_id]
                fetched_at_str = str(entry.get("fetched_at") or "")
                try:
                    fetched_at = datetime.fromisoformat(fetched_at_str)
                    if fetched_at.tzinfo is None:
                        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError):
                    del self._entries[chat_id]
                    continue
                if now - fetched_at > timedelta(hours=ttl_hours):
                    del self._entries[chat_id]
                    continue
                result.append(dict(entry))
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
                "chat_capability_cache_load_failed",
                path=str(path),
                error=str(exc),
            )
            return
        if not isinstance(raw, dict):
            logger.warning("chat_capability_cache_load_malformed", path=str(path))
            return
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            self._entries[str(key)] = dict(value)
            loaded += 1
        if loaded or skipped:
            logger.info("chat_capability_cache_loaded", loaded=loaded, skipped=skipped)

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
        except OSError as exc:
            logger.warning(
                "chat_capability_cache_persist_failed",
                path=str(path),
                error=str(exc),
            )


# Module-level singleton — pattern совпадает с chat_ban_cache / silence_manager.
chat_capability_cache = ChatCapabilityCache()
