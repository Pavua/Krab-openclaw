# -*- coding: utf-8 -*-
"""
Трекер присутствия владельца (owner presence) для Idea 17 — Smart auto-respond
when offline.

Зачем это существует:

Когда Pavel (owner) не онлайн >2 часов и кто-то пишет ему в DM, Краб должен
отправить вежливый holdover («Передам Pavel, он сейчас не онлайн»), а само
сообщение прокинуть в high-priority inbox с пометкой `auto_holdover`. Это
снимает социальное давление с собеседника (он видит, что не игнорят), и
гарантирует, что Pavel ничего не пропустит.

Чтобы определить «онлайн / офлайн», достаточно отслеживать timestamp последней
активности owner — это могут быть:
  - его исходящие сообщения через userbot (out=True);
  - явные heartbeat от Telegram (`updates.UserStatus`);
  - owner panel pings (TODO в backlog).

Этот модуль НЕ занимается hook'ами в bridge — он лишь предоставляет thin
singleton с двумя операциями: «запиши, что owner был активен» и «спроси,
оффлайн ли он сейчас». Wire-up оставлен в backlog.

### Инварианты

- **Idempotent record.** `record_owner_seen()` несколько раз подряд — просто
  обновление `last_seen_at`. Никакого счётчика / истории.
- **Persist per write.** После каждого `record_owner_seen()` файл переписывается.
  В отличие от чат-кэшей, owner-апдейтов очень мало (только из активности
  владельца), поэтому диск не страдает.
- **Lazy load.** При первом `is_offline()` после bootstrap — читаем файл.
  Если файла нет, считаем что owner online (consistent с «холодный старт после
  только что закрытого Краба»).

### Не решает (out of scope, backlog)
- Hook в `userbot_bridge` (incoming DM detection + holdover send).
- Persona-aware drift у текста ответа (есть hint через
  `pick_holdover_message`, но без LLM).
- Per-user override (не слать никогда, слать всегда). Будет поверх в Idea 18.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Порог по умолчанию (минут): сколько тишины со стороны owner считаем «офлайн».
# Идея 17 фиксирует 2 часа как «достаточно долго чтобы собеседнику стало
# непонятно, что происходит». Конфигурируется через env (см. wire-up).
_DEFAULT_OFFLINE_THRESHOLD_MIN: float = 120.0

# Wave 24-A pattern: warning порог при медленной загрузке с диска.
_SLOW_LOAD_WARN_MS: float = 500.0

# Заготовки holdover-фраз, три персона-варианта. Намеренно коротко и без
# emoji — чтобы не выбиваться из стиля чата. Вызывающий код выбирает вариант
# (например, по chat_id mod 3 или по persona profile).
HOLDOVER_MESSAGES: dict[str, str] = {
    "casual": ("Привет! Pavel сейчас не на связи, но я передам ему как только он появится."),
    "formal": (
        "Здравствуйте. Павел временно недоступен, ваше сообщение будет передано "
        "ему сразу же, как только он вернётся в сеть."
    ),
    "business": (
        "Pavel сейчас офлайн. Сообщение получено и поставлено в приоритет — "
        "он ответит, как только освободится."
    ),
}


def pick_holdover_message(persona: str | None = None) -> str:
    """Возвращает текст holdover'а по персоне; fallback — casual.

    persona: один из ключей HOLDOVER_MESSAGES (`casual`/`formal`/`business`).
    Любое неизвестное значение → casual (defensive). Это thin helper, чтобы
    bridge мог импортировать без знания внутренней структуры словаря.
    """
    key = (persona or "casual").strip().lower()
    return HOLDOVER_MESSAGES.get(key, HOLDOVER_MESSAGES["casual"])


class OwnerPresenceTracker:
    """Потокобезопасный трекер «когда owner последний раз был онлайн».

    Используется как module-level singleton (`owner_presence_tracker` ниже).
    Принимает `storage_path` и `now_fn` в конструкторе только для unit-тестов;
    в рантайме singleton инициализируется через `configure_default_path()`.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._last_seen_at: datetime | None = None
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Перенастраивает путь и подгружает state с диска.

        Вызывается один раз из bootstrap userbot (после того как
        `_runtime_state_dir` создан).
        """
        with self._lock:
            self._storage_path = storage_path
            self._last_seen_at = None
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_owner_seen(self, when: datetime | None = None) -> None:
        """Помечает owner как «только что был активен».

        `when` опционально для тестов и backfill (при загрузке исторических
        update'ов). По умолчанию — `now()`.
        """
        ts = when if when is not None else self._now()
        # Защита от tz-naive datetime: если кто-то передал naive, считаем UTC.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        with self._lock:
            # Не двигаем last_seen_at назад: backfill старого heartbeat не
            # должен «забывать» более свежую активность.
            if self._last_seen_at is not None and ts < self._last_seen_at:
                return
            self._last_seen_at = ts
            self._persist_to_disk()
        logger.debug("owner_presence_recorded", last_seen_at=ts.isoformat())

    def is_offline(self, threshold_min: float = _DEFAULT_OFFLINE_THRESHOLD_MIN) -> bool:
        """True → owner молчал дольше `threshold_min` минут.

        Если we never recorded heartbeat (после wipe state), считаем owner
        онлайн (False) — лучше пропустить holdover, чем отправить «он офлайн»
        в первые секунды после рестарта Краба.
        """
        with self._lock:
            last = self._last_seen_at
        if last is None:
            return False
        delta = self._now() - last
        return delta >= timedelta(minutes=float(threshold_min))

    def last_seen_at(self) -> datetime | None:
        """Снимок текущего last_seen_at (или None если не записан)."""
        with self._lock:
            return self._last_seen_at

    def offline_duration_minutes(self) -> float | None:
        """Сколько минут owner молчит. None если heartbeat'а ещё не было."""
        with self._lock:
            last = self._last_seen_at
        if last is None:
            return None
        return (self._now() - last).total_seconds() / 60.0

    def reset(self) -> None:
        """Полная очистка state (для тестов / `!presence reset` при необходимости)."""
        with self._lock:
            self._last_seen_at = None
            self._persist_to_disk()
        logger.info("owner_presence_reset")

    # ---- Internal helpers -----------------------------------------------

    def _load_from_disk(self) -> None:
        t0 = time.monotonic()
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.warning(
                "owner_presence_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
                elapsed_ms=elapsed_ms,
            )
            return
        if not isinstance(raw, dict):
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            logger.warning("owner_presence_load_malformed", path=str(path), elapsed_ms=elapsed_ms)
            return
        last_seen_iso = raw.get("last_seen_at")
        if isinstance(last_seen_iso, str) and last_seen_iso:
            try:
                self._last_seen_at = datetime.fromisoformat(last_seen_iso)
            except (TypeError, ValueError):
                logger.warning("owner_presence_entry_corrupt", path=str(path), raw=last_seen_iso)
                self._last_seen_at = None
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "owner_presence_loaded",
            has_state=self._last_seen_at is not None,
            elapsed_ms=elapsed_ms,
        )
        if elapsed_ms > _SLOW_LOAD_WARN_MS:
            logger.warning(
                "owner_presence_slow_load",
                elapsed_ms=elapsed_ms,
                threshold_ms=_SLOW_LOAD_WARN_MS,
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "last_seen_at": self._last_seen_at.isoformat()
                if self._last_seen_at is not None
                else None,
            }
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "owner_presence_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — паттерн совпадает с silence_manager / inbox_service /
# chat_ban_cache. Конкретный путь конфигурируется в bootstrap через
# `owner_presence_tracker.configure_default_path(...)`.
owner_presence_tracker = OwnerPresenceTracker()
