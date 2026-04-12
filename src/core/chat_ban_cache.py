# -*- coding: utf-8 -*-
"""
Персистентный short-circuit кэш для чатов, где Telegram API стабильно возвращает
отказ на send (`USER_BANNED_IN_CHANNEL`, `ChatWriteForbidden`, `UserDeactivated`).

Зачем это существует:

После chat-level бана в группе (09.04.2026 How2AI, yung_nagato) Краб всё равно
проходит полный LLM flow: триггер → openclaw stream → Gemini генерирует ответ →
попытка `send_message` → `UserBannedInChannel` → traceback в лог. Это плохо по
трём причинам сразу:

1. **Жжёт Gemini квоту.** Каждое сообщение в забаненный чат — это ~1500-3000
   prompt токенов, которые всё равно пропадают. При агрессивных группах это
   десятки KT в день.
2. **Увеличивает нагрузку на Telegram API.** Каждая отказанная попытка — это
   `send_message` RPC + (иногда) несколько retry. Telegram SpamBot смотрит на
   `messages/sec` аккаунта и может продлить глобальный limit.
3. **Маскирует реальные ошибки в логах.** `background_ai_request_failed` с
   повторяющимся `UserBannedInChannel` засоряет стэктрейсы и ломает grep'ы.

Решение: после первого отказа запомнить `chat_id → (error_code, banned_at,
expires_at)` в persisted JSON. На входе в `_process_message` проверять — если
чат в cache и не истёк, просто return без LLM/Telegram activity.

### Инварианты

- **Идемпотентно.** `mark_banned(same_chat)` несколько раз за окно — один
  effective mark. Повторный mark в том же окне обновляет `last_seen_at` но не
  двигает `expires_at` (иначе ban становится permanent).
- **Persist per write.** После каждого `mark_banned` / `clear` файл
  переписывается. Приемлемо: writes редкие (раз в часы), чтение — hot path,
  и оно идёт из in-memory dict, не с диска.
- **Expiry check ленивый.** `is_banned(chat_id)` читает `expires_at` и
  возвращает False если время прошло. Background task для подчистки не нужен —
  next `load_from_disk` убирает expired записи.

### Не решает
- Не защищает от FloodWait (это B.4 voice blocklist / будущий B.5 debounce).
- Не кеширует capability info (`get_chat`, slow_mode). Это B.6.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Telegram error codes, которые означают «на этот чат слать бесполезно».
# Используются только как whitelist для mark_banned; сам cache хранит любой
# код, который передали вручную (unit tests / future extension).
BANNED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "UserBannedInChannel",
        "ChatWriteForbidden",
        "UserDeactivated",
        "ChannelPrivate",
        "ChatRestricted",
    }
)

# Default cooldown: 6 часов. Конфигурируется через
# `config.CHAT_BAN_CACHE_COOLDOWN_HOURS`, но дефолт держим в модуле, чтобы
# модуль работал и в тестах где config может быть недоступен.
_DEFAULT_COOLDOWN_HOURS: float = 6.0


class ChatBanCache:
    """Потокобезопасный ban cache с persist на диск.

    Используется как module-level singleton (`chat_ban_cache` ниже). Принимает
    `storage_path` в конструкторе ТОЛЬКО для unit-тестов; в рантайме singleton
    инициализируется через `configure_default_path()`.
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
        # Инжектируемый источник времени: нужен тестам, чтобы подменять
        # «сейчас» без monkeypatch модуля и без прямой мутации _entries.
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает то что лежит на диске.

        Вызывается один раз при bootstrap (из userbot_bridge или bootstrap/runtime).
        Если cache уже был настроен, новый путь переконфигурирует singleton и
        перезагружает записи — нужно для тестов и для re-init после переезда.
        """
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def is_banned(self, chat_id: Any) -> bool:
        """True → в этот чат слать нельзя (cache активен, не истёк).

        Ленивый expiry: если запись протухла, она тихо удаляется из памяти
        (но НЕ persist'ится сразу — persist только на mark/clear, чтобы не
        писать диск на каждый hot-path check).
        """
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return False
            expires_at = entry.get("expires_at")
            if expires_at is None:
                # Permanent ban marker — не истекает. Используется для ручных
                # owner override через `mark_banned(..., cooldown_hours=None)`.
                return True
            try:
                expires = datetime.fromisoformat(expires_at)
            except (TypeError, ValueError):
                # Битая запись → считаем её невалидной, очищаем чтобы не
                # накапливать мусор. Логируем raw чтобы диагностировать
                # источник порчи (ручное редактирование, старый формат).
                logger.warning(
                    "chat_ban_cache_entry_corrupt",
                    chat_id=target,
                    raw=repr(entry),
                )
                del self._entries[target]
                return False
            if self._now() >= expires:
                del self._entries[target]
                return False
            return True

    def mark_banned(
        self,
        chat_id: Any,
        error_code: str,
        *,
        cooldown_hours: float | None = _DEFAULT_COOLDOWN_HOURS,
    ) -> None:
        """Помечает чат как забаненный и persist'ит в JSON.

        `cooldown_hours=None` → permanent mark (до ручного `clear`). Нужен
        для редкого случая: owner знает что чат мёртв и не хочет ждать.
        Обычно используется default 6 часов — Telegram обычно снимает
        временные ограничения в пределах нескольких часов, если причина ушла.
        """
        target = self._normalize(chat_id)
        if not target:
            return
        normalized_code = str(error_code or "unknown").strip() or "unknown"
        now = self._now()
        expires_iso: str | None
        if cooldown_hours is None:
            expires_iso = None
        else:
            expires_iso = (now + timedelta(hours=float(cooldown_hours))).isoformat()

        with self._lock:
            existing = self._entries.get(target)
            if existing is not None:
                # Идемпотентный повторный mark в том же окне: обновляем
                # last_seen_at / count, НО не двигаем expires_at чтобы
                # многократные отказы не сделали ban эффективно permanent.
                existing["last_seen_at"] = now.isoformat()
                existing["hit_count"] = int(existing.get("hit_count") or 0) + 1
                existing["last_error_code"] = normalized_code
            else:
                self._entries[target] = {
                    "error_code": normalized_code,
                    "banned_at": now.isoformat(),
                    "last_seen_at": now.isoformat(),
                    "expires_at": expires_iso,
                    "hit_count": 1,
                    "last_error_code": normalized_code,
                }
            self._persist_to_disk()
        logger.info(
            "chat_ban_cache_marked",
            chat_id=target,
            error_code=normalized_code,
            cooldown_hours=cooldown_hours,
        )

    def clear(self, chat_id: Any) -> bool:
        """Удаляет запись для чата. Возвращает True если была запись."""
        target = self._normalize(chat_id)
        if not target:
            return False
        with self._lock:
            if target not in self._entries:
                return False
            del self._entries[target]
            self._persist_to_disk()
        logger.info("chat_ban_cache_cleared", chat_id=target)
        return True

    def list_entries(self) -> list[dict[str, Any]]:
        """Снимок текущих записей для owner UI / `!chatban status` команды.

        Возвращает копии dict'ов чтобы caller не мутировал внутреннее состояние.
        Ленивый expiry applied: записи с истёкшим expires_at не попадут в output.
        """
        now = self._now()
        result: list[dict[str, Any]] = []
        # Если были evictions — сразу persist'им, иначе на рестарте те же
        # протухшие записи снова вернутся с диска и снова будут вычищаться
        # в бесконечном цикле cleanup'а.
        _evicted_any: bool = False
        with self._lock:
            for chat_id in list(self._entries.keys()):
                entry = self._entries[chat_id]
                expires_at = entry.get("expires_at")
                if expires_at is not None:
                    try:
                        if now >= datetime.fromisoformat(expires_at):
                            del self._entries[chat_id]
                            _evicted_any = True
                            continue
                    except (TypeError, ValueError):
                        logger.warning(
                            "chat_ban_cache_entry_corrupt",
                            chat_id=chat_id,
                            raw=repr(entry),
                        )
                        del self._entries[chat_id]
                        _evicted_any = True
                        continue
                snapshot = dict(entry)
                snapshot["chat_id"] = chat_id
                result.append(snapshot)
            if _evicted_any:
                self._persist_to_disk()
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
                "chat_ban_cache_load_failed",
                path=str(path),
                error=str(exc),
            )
            return
        if not isinstance(raw, dict):
            logger.warning("chat_ban_cache_load_malformed", path=str(path))
            return
        now = self._now()
        loaded = 0
        skipped = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            expires_at = value.get("expires_at")
            if expires_at is not None:
                try:
                    if now >= datetime.fromisoformat(expires_at):
                        skipped += 1
                        continue
                except (TypeError, ValueError):
                    skipped += 1
                    continue
            self._entries[str(key)] = dict(value)
            loaded += 1
        if loaded or skipped:
            logger.info("chat_ban_cache_loaded", loaded=loaded, skipped=skipped)

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
        # TypeError ловим чтобы будущий баг с не-ISO datetime в entry
        # (например, сырой datetime объект вместо isoformat) не ронял
        # mark_banned в hot path. Лучше потерять write, чем вылететь.
        except (OSError, TypeError) as exc:
            logger.warning(
                "chat_ban_cache_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с silence_manager, inbox_service,
# krab_scheduler. Конкретный путь конфигурируется вызовом
# `chat_ban_cache.configure_default_path(...)` из bootstrap.
chat_ban_cache = ChatBanCache()
