# -*- coding: utf-8 -*-
"""
Wave 95: content-hash cache для translator_engine.translate_text.

Цель — экономить Gemini-квоту на повторяющихся фразах (стандартные ответы,
идиомы, "ok", "спасибо", typical clipboard). Каждый LLM-вызов в translator
сейчас уходит на flash-tier 200-600 мс + tokens. Hit-rate 30-50% ожидаем.

Архитектура:

- Key: ``sha256(source_text + "\\n" + target_lang)[:16]`` — 64-битный хеш,
  collision-resistant для ~5k entries.
- Value: ``{translation, ts, hit_count}``.
- LRU + TTL: max 5000 entries, TTL 7 дней (default, конфигурируется через
  конструктор для тестов).
- Persist: atomic write в ``~/.openclaw/krab_runtime_state/translation_cache.json``
  через tempfile + ``os.replace``. Write batched: на каждый store(), но не
  на lookup() (hot path).
- Thread-safe: `threading.RLock` — translator вызывается из event-loop, но
  background sweep / metrics scrape могут читать параллельно.

Env-gate: ``KRAB_TRANSLATION_CACHE_ENABLED`` (default "1"). Если "0" —
lookup всегда возвращает None, store no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .logger import get_logger
from .metrics.translation import (
    krab_translation_cache_hits_total,
    krab_translation_cache_misses_total,
    krab_translation_cache_size,
)

logger = get_logger(__name__)


# Дефолты — продовые значения. Тесты могут переопределять через конструктор.
_DEFAULT_MAX_ENTRIES: int = 5000
_DEFAULT_TTL_SECONDS: float = 7 * 24 * 3600.0  # 7 дней


def _env_enabled() -> bool:
    """Читает env-gate на каждом вызове — даёт runtime toggle без рестарта."""
    raw = os.getenv("KRAB_TRANSLATION_CACHE_ENABLED", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _hash_key(text: str, target_lang: str) -> str:
    """SHA256(text + \\n + tgt)[:16] — стабильный 16-hex key."""
    raw = (text or "").strip() + "\n" + (target_lang or "").strip().lower()
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    return digest[:16]


class TranslationCache:
    """LRU + TTL persistent cache переводов.

    Singleton используется как module-level ``translation_cache`` ниже.
    Конструктор принимает параметры только для тестов; в рантайме путь
    конфигурируется через ``configure_default_path()`` из bootstrap.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        self._max_entries: int = int(max_entries)
        self._ttl_seconds: float = float(ttl_seconds)
        self._now_fn: Callable[[], float] = now_fn or time.time
        # OrderedDict даёт O(1) LRU: move_to_end на hit, popitem(last=False) на eviction.
        self._entries: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
        if storage_path is not None:
            self._load_from_disk()
            self._update_size_gauge()

    # ---- Public API -----------------------------------------------------

    def lookup(self, text: str, target_lang: str) -> str | None:
        """Возвращает cached translation или None.

        Считает hit/miss в prometheus. Если env-gate выключен — всегда None
        и miss НЕ инкрементируется (cache disabled, не «реальный miss»).
        """
        if not _env_enabled():
            return None
        key = _hash_key(text, target_lang)
        now = self._now_fn()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                _safe_metric_inc(krab_translation_cache_misses_total)
                return None
            ts = float(entry.get("ts") or 0.0)
            if self._ttl_seconds > 0 and (now - ts) >= self._ttl_seconds:
                # Истёкший entry — удаляем и считаем как miss.
                del self._entries[key]
                self._update_size_gauge_locked()
                _safe_metric_inc(krab_translation_cache_misses_total)
                return None
            # LRU touch + hit_count++.
            self._entries.move_to_end(key)
            entry["hit_count"] = int(entry.get("hit_count") or 0) + 1
            translation = entry.get("translation")
        _safe_metric_inc(krab_translation_cache_hits_total)
        return translation if isinstance(translation, str) else None

    def store(self, text: str, target_lang: str, translation: str) -> None:
        """Сохраняет перевод в cache + persist на диск.

        No-op если env-gate выключен или ``translation`` пустой.
        """
        if not _env_enabled():
            return
        if not translation or not translation.strip():
            return
        key = _hash_key(text, target_lang)
        now = self._now_fn()
        with self._lock:
            self._entries[key] = {
                "translation": translation,
                "ts": now,
                "hit_count": 0,
            }
            self._entries.move_to_end(key)
            # LRU eviction если переполнили.
            while len(self._entries) > self._max_entries:
                evicted_key, _evicted_val = self._entries.popitem(last=False)
                logger.info(
                    "translation_cache_evicted",
                    key=evicted_key,
                    cache_size=len(self._entries),
                )
            self._update_size_gauge_locked()
            self._persist_to_disk()

    def stats(self) -> dict[str, Any]:
        """Снимок состояния для /api/translation/cache/stats."""
        with self._lock:
            size = len(self._entries)
            total_hits = sum(int(e.get("hit_count") or 0) for e in self._entries.values())
        return {
            "enabled": _env_enabled(),
            "size": size,
            "max_entries": self._max_entries,
            "ttl_seconds": self._ttl_seconds,
            "total_lifetime_hits": total_hits,
            "storage_path": str(self._storage_path) if self._storage_path else None,
        }

    def clear(self) -> int:
        """Очищает cache (in-memory + disk). Возвращает число удалённых записей."""
        with self._lock:
            n = len(self._entries)
            self._entries.clear()
            self._update_size_gauge_locked()
            self._persist_to_disk()
        logger.info("translation_cache_cleared", removed=n)
        return n

    # ---- Bootstrap configuration ---------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает с диска.

        Идемпотентно — повторный вызов перечитывает state. Используется
        в bootstrap (userbot_bridge.start) и в тестах для re-init.
        """
        with self._lock:
            self._storage_path = storage_path
            self._entries = OrderedDict()
            self._load_from_disk()
            self._update_size_gauge_locked()

    # ---- Internal ------------------------------------------------------

    def _update_size_gauge(self) -> None:
        with self._lock:
            self._update_size_gauge_locked()

    def _update_size_gauge_locked(self) -> None:
        try:
            krab_translation_cache_size.set(float(len(self._entries)))
        except Exception:  # pragma: no cover — no-op заглушка может не иметь .set
            pass

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "translation_cache_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("translation_cache_load_malformed", path=str(path))
            return
        entries_raw = raw.get("entries") if "entries" in raw else raw
        if not isinstance(entries_raw, dict):
            return
        now = self._now_fn()
        loaded = 0
        skipped = 0
        # Сохраняем порядок из disk (recently used последними).
        for key, value in entries_raw.items():
            if not isinstance(value, dict):
                skipped += 1
                continue
            ts = value.get("ts")
            if not isinstance(ts, (int, float)):
                skipped += 1
                continue
            if self._ttl_seconds > 0 and (now - float(ts)) >= self._ttl_seconds:
                skipped += 1
                continue
            translation = value.get("translation")
            if not isinstance(translation, str):
                skipped += 1
                continue
            self._entries[str(key)] = {
                "translation": translation,
                "ts": float(ts),
                "hit_count": int(value.get("hit_count") or 0),
            }
            loaded += 1
            if loaded >= self._max_entries:
                break
        if loaded or skipped:
            logger.info(
                "translation_cache_loaded",
                loaded=loaded,
                skipped=skipped,
                path=str(path),
            )

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        payload = {
            "version": 1,
            "entries": dict(self._entries),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: tempfile в той же директории + os.replace.
            fd, tmp_path = tempfile.mkstemp(
                prefix=".translation_cache.",
                suffix=".tmp",
                dir=str(path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                os.replace(tmp_path, path)
            except Exception:
                # Удаляем tmp если os.replace не успел.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, TypeError) as exc:
            logger.warning(
                "translation_cache_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


def _safe_metric_inc(counter: Any) -> None:
    """Толерантный inc — no-op заглушки могут не иметь .inc()."""
    try:
        counter.inc()
    except Exception:  # pragma: no cover
        pass


# Module-level singleton — pattern совпадает с chat_ban_cache / inbox_service.
translation_cache = TranslationCache()
