# -*- coding: utf-8 -*-
"""
Persistent history store для model switch операций — Wave 145 (Session 53).

Хранит FIFO last 100 переключений primary модели через POST /api/admin/model/switch
(Wave 144 UI picker) + /api/model/switch (legacy slot-oriented) + любых вручную
вызванных ``model_manager.set_model`` / ``set_provider`` (если caller добавит
``log_switch`` после).

Зачем отдельный store:

В Wave 144 history бралось из ``black_box.tail_events(kind="model_switch")``,
но это требовало работающий BlackBox dependency и формат записи зависел от
``log_event(kind, detail)`` (string-based). У нас нет гарантии что BlackBox
доступен (зависит от service orchestration boot order), а UI должен показывать
структурированные поля (from_provider/model, to_provider/model, actor, reason,
success). JSON store решает обе проблемы: ленивый load, atomic write, FIFO 100,
структурированный schema.

### Инварианты
- **Atomic write**: tempfile + os.replace в той же директории → safe для
  concurrent writes из cron + ручных команд.
- **FIFO 100**: после `log_switch` history обрезается до последних 100 записей.
  Достаточно для UI "Recent switches" + диагностики (~неделя при 14 switches/day).
- **Lazy init**: первый `log_switch` или `query_recent` создаёт parent dir.
- **Graceful degradation**: corrupt JSON → warn + treat as empty.
- **Schema-stable**: все поля required → consumers могут полагаться на формат.

### Schema (entry)
```
{
  "ts": "2026-05-12T20:30:00+00:00",
  "by": "owner_panel|cli|cron|unknown",
  "from_provider": "google-vertex",
  "from_model": "google-vertex/gemini-3-pro-preview",
  "to_provider": "anthropic-vertex",
  "to_model": "anthropic-vertex/claude-sonnet-4-5",
  "reason": "manual_switch|quota_exhausted|fallback|...",
  "success": true
}
```
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)


# Максимум записей в FIFO буфере. 100 ≈ неделя при средней частоте 14
# переключений/день (cloud→cli fallback + manual UI + cron health switches).
_MAX_ENTRIES: int = 100

# Сколько записей возвращает `query_recent` по умолчанию. 20 показывается в UI
# History section. Старая Wave 144 показывала 10 — оставляем 10 как default
# для registry endpoint (см. _format_history_entries в models_admin_router).
_DEFAULT_QUERY_LIMIT: int = 20


class ModelSwitchHistory:
    """Persistent FIFO store последних model switch операций.

    Singleton-pattern (см. модуль-level instance ниже). Storage path
    устанавливается через `configure_default_path(...)` из bootstrap.
    Тесты создают свой instance с custom `storage_path` чтобы не трогать
    глобальный singleton.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._entries: list[dict[str, Any]] = []
        # Инжектируемый источник времени для unit tests.
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        if storage_path is not None:
            self._load_from_disk()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и загружает существующие записи.

        Вызывается один раз при bootstrap (из userbot_bridge или
        bootstrap/runtime).
        """
        with self._lock:
            self._storage_path = storage_path
            self._entries = []
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def log_switch(
        self,
        *,
        by: str,
        from_provider: str | None,
        from_model: str | None,
        to_provider: str | None,
        to_model: str | None,
        reason: str = "",
        success: bool = True,
    ) -> dict[str, Any]:
        """Записывает switch entry и persist'ит на диск.

        Все поля trim'аются до строк (None → пустая строка). FIFO trim до
        `_MAX_ENTRIES` после append. Возвращает копию записи.
        """
        entry: dict[str, Any] = {
            "ts": self._now_fn().isoformat(),
            "by": str(by or "unknown").strip() or "unknown",
            "from_provider": str(from_provider or "").strip(),
            "from_model": str(from_model or "").strip(),
            "to_provider": str(to_provider or "").strip(),
            "to_model": str(to_model or "").strip(),
            "reason": str(reason or "").strip(),
            "success": bool(success),
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > _MAX_ENTRIES:
                # FIFO drop oldest. del slice вместо pop(0) циклом — O(n) один
                # раз вместо O(n²).
                drop = len(self._entries) - _MAX_ENTRIES
                del self._entries[:drop]
            self._persist_to_disk()
        logger.info(
            "model_switch_history_logged",
            by=entry["by"],
            from_model=entry["from_model"] or "-",
            to_model=entry["to_model"] or "-",
            success=entry["success"],
        )
        return dict(entry)

    def query_recent(self, limit: int = _DEFAULT_QUERY_LIMIT) -> list[dict[str, Any]]:
        """Возвращает копии последних `limit` записей (newest last).

        `limit <= 0` интерпретируется как 0 (пустой список). `limit > len` →
        вернёт всё что есть.
        """
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = _DEFAULT_QUERY_LIMIT
        if n <= 0:
            return []
        with self._lock:
            tail = self._entries[-n:]
            return [dict(e) for e in tail]

    def to_json_safe(self, limit: int = _DEFAULT_QUERY_LIMIT) -> list[dict[str, Any]]:
        """Сериализуемый список для API responses.

        Семантически = `query_recent`, но возвращает простые dict[str, Any]
        с гарантированно JSON-serialisable значениями (все поля уже string/bool).
        Используется в `/api/models/registry`.
        """
        return self.query_recent(limit=limit)

    def clear(self) -> int:
        """Полная очистка истории. Возвращает количество удалённых записей.

        Используется в тестах и (теоретически) в owner panel "wipe history".
        """
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._persist_to_disk()
        if count:
            logger.info("model_switch_history_cleared", count=count)
        return count

    def size(self) -> int:
        """Текущее количество записей (не превышает `_MAX_ENTRIES`)."""
        with self._lock:
            return len(self._entries)

    # ---- Internal helpers -----------------------------------------------

    def _load_from_disk(self) -> None:
        """Синхронная загрузка с диска. Corrupt JSON → warn + empty state."""
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "[]")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "model_switch_history_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, list):
            logger.warning(
                "model_switch_history_load_malformed",
                path=str(path),
                kind=type(raw).__name__,
            )
            return
        loaded: list[dict[str, Any]] = []
        skipped = 0
        for value in raw:
            if not isinstance(value, dict):
                skipped += 1
                continue
            # Дефенсивная нормализация: пропущенные поля → пустые строки.
            loaded.append(
                {
                    "ts": str(value.get("ts") or ""),
                    "by": str(value.get("by") or "unknown"),
                    "from_provider": str(value.get("from_provider") or ""),
                    "from_model": str(value.get("from_model") or ""),
                    "to_provider": str(value.get("to_provider") or ""),
                    "to_model": str(value.get("to_model") or ""),
                    "reason": str(value.get("reason") or ""),
                    "success": bool(value.get("success", True)),
                }
            )
        # На случай если кто-то редактировал файл и вышел за FIFO лимит —
        # выкидываем самые старые при load.
        if len(loaded) > _MAX_ENTRIES:
            drop = len(loaded) - _MAX_ENTRIES
            loaded = loaded[drop:]
        self._entries = loaded
        if loaded or skipped:
            logger.info(
                "model_switch_history_loaded",
                loaded=len(loaded),
                skipped=skipped,
            )

    def _persist_to_disk(self) -> None:
        """Atomic write через tempfile + os.replace (skill_curator_state pattern)."""
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # tempfile в той же директории чтобы os.replace был atomic
            # (cross-device replace падает с OSError).
            tmp_fd, tmp_name = tempfile.mkstemp(
                prefix=".history.", suffix=".tmp", dir=str(path.parent)
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                    json.dump(self._entries, fh, ensure_ascii=False, indent=2)
                os.replace(tmp_name, path)
            except Exception:
                # На любую ошибку чистим temp-файл — иначе накопится мусор.
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except (OSError, TypeError) as exc:
            logger.warning(
                "model_switch_history_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с chat_ban_cache,
# inbox_service, silence_manager. Конкретный путь конфигурируется через
# `model_switch_history.configure_default_path(...)` из bootstrap.
model_switch_history = ModelSwitchHistory()
