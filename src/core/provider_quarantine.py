# -*- coding: utf-8 -*-
"""
Wave 94: provider quarantine — авто-приостановка проблемных провайдеров.

Контекст:
    Иногда провайдер (Anthropic Vertex, OpenAI codex-cli, google-direct) даёт
    серию подряд идущих fail'ов — auth/quota/network. Сейчас Krab продолжает
    retry'ить и жжёт время. Wave 62-G сделал частный pattern для codex_quota
    (persist disabled flag). Wave 94 обобщает: для ЛЮБОГО провайдера копим
    recent failures → temporary quarantine 5 мин.

Алгоритм:
    1. record_provider_failure(provider, error_class) — append timestamp
    2. Если в окне 10 мин ≥5 failures → quarantine на 5 мин (expires_at)
    3. is_provider_quarantined(provider) — read+lazy-expire
    4. record_provider_success(provider) — reset failures + снимает quarantine

State в ~/.openclaw/krab_runtime_state/provider_quarantine.json.
Module-level singleton `provider_quarantine`.
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


# Порог: 5 fail'ов в окне 10 мин → quarantine на 5 мин.
_DEFAULT_FAILURE_THRESHOLD: int = 5
_DEFAULT_WINDOW_SECONDS: float = 600.0  # 10 мин
_DEFAULT_QUARANTINE_SECONDS: float = 300.0  # 5 мин


class ProviderQuarantine:
    """Потокобезопасный quarantine-кэш по провайдерам.

    Используется как module-level singleton (`provider_quarantine`). Принимает
    `storage_path` в конструкторе ТОЛЬКО для unit-тестов; в рантайме singleton
    инициализируется через `configure_default_path()`.
    """

    def __init__(
        self,
        *,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        window_seconds: float = _DEFAULT_WINDOW_SECONDS,
        quarantine_seconds: float = _DEFAULT_QUARANTINE_SECONDS,
    ) -> None:
        self._lock = threading.RLock()
        self._storage_path: Path | None = storage_path
        # Структура: provider → {"failures": [iso, ...], "quarantined_until": iso|None,
        #                        "last_reason": str|None}
        self._entries: dict[str, dict[str, Any]] = {}
        self._now_fn: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        self._failure_threshold = max(1, int(failure_threshold))
        self._window_seconds = max(1.0, float(window_seconds))
        self._quarantine_seconds = max(1.0, float(quarantine_seconds))
        if storage_path is not None:
            self._load_from_disk()

    def _now(self) -> datetime:
        return self._now_fn()

    # ---- Configuration --------------------------------------------------

    def configure_default_path(self, storage_path: Path) -> None:
        """Устанавливает путь к persisted JSON и подгружает что лежит на диске."""
        with self._lock:
            self._storage_path = storage_path
            self._entries = {}
            self._load_from_disk()

    # ---- Core API -------------------------------------------------------

    def record_provider_failure(
        self,
        provider: str,
        error_class: str | None = None,
    ) -> bool:
        """Записывает fail. Возвращает True если в результате провайдер
        попал в quarantine (transition fail→quarantined)."""
        target = self._normalize(provider)
        if not target:
            return False
        now = self._now()
        reason = str(error_class or "unknown").strip() or "unknown"
        transitioned = False
        with self._lock:
            entry = self._entries.setdefault(
                target,
                {"failures": [], "quarantined_until": None, "last_reason": None},
            )
            failures: list[str] = list(entry.get("failures") or [])
            # Чистим failures за окном.
            cutoff = now - timedelta(seconds=self._window_seconds)
            failures = [
                ts
                for ts in failures
                if self._safe_parse(ts) and self._safe_parse(ts) >= cutoff  # type: ignore[operator]
            ]
            failures.append(now.isoformat())
            entry["failures"] = failures
            entry["last_reason"] = reason
            already_quarantined = self._is_active_quarantine(entry, now)
            if not already_quarantined and len(failures) >= self._failure_threshold:
                expires = now + timedelta(seconds=self._quarantine_seconds)
                entry["quarantined_until"] = expires.isoformat()
                transitioned = True
            self._persist_to_disk()
        if transitioned:
            logger.warning(
                "provider_quarantine_triggered",
                provider=target,
                reason=reason,
                failure_count=len(failures),
                window_seconds=self._window_seconds,
                quarantine_seconds=self._quarantine_seconds,
            )
            # Метрики — best-effort, без падения hot path.
            try:
                from .metrics.quarantine import record_quarantine_event

                record_quarantine_event(provider=target, reason=reason, quarantined=True)
            except Exception:  # noqa: BLE001
                pass
        else:
            logger.debug(
                "provider_quarantine_failure_recorded",
                provider=target,
                reason=reason,
                failure_count=len(failures),
            )
        return transitioned

    def record_provider_success(self, provider: str) -> None:
        """Сбрасывает failures и снимает quarantine для провайдера."""
        target = self._normalize(provider)
        if not target:
            return
        was_quarantined = False
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return
            was_quarantined = self._is_active_quarantine(entry, self._now())
            entry["failures"] = []
            entry["quarantined_until"] = None
            self._persist_to_disk()
        if was_quarantined:
            logger.info("provider_quarantine_cleared", provider=target)
            try:
                from .metrics.quarantine import record_quarantine_event

                record_quarantine_event(provider=target, reason="cleared", quarantined=False)
            except Exception:  # noqa: BLE001
                pass

    def is_provider_quarantined(self, provider: str) -> bool:
        """True если провайдер в активном quarantine. Lazy-expire истёкших."""
        target = self._normalize(provider)
        if not target:
            return False
        now = self._now()
        with self._lock:
            entry = self._entries.get(target)
            if entry is None:
                return False
            if self._is_active_quarantine(entry, now):
                return True
            # Истёк — чистим маркер, но failures оставляем (могут ещё быть
            # в окне; следующий fail вычистит сам).
            if entry.get("quarantined_until") is not None:
                entry["quarantined_until"] = None
                self._persist_to_disk()
                logger.info("provider_quarantine_expired", provider=target)
                try:
                    from .metrics.quarantine import record_quarantine_event

                    record_quarantine_event(provider=target, reason="expired", quarantined=False)
                except Exception:  # noqa: BLE001
                    pass
            return False

    def list_entries(self) -> list[dict[str, Any]]:
        """Снимок записей для owner UI / status команд. Возвращает копии."""
        now = self._now()
        result: list[dict[str, Any]] = []
        with self._lock:
            for provider, entry in self._entries.items():
                snapshot = dict(entry)
                snapshot["provider"] = provider
                snapshot["failures"] = list(entry.get("failures") or [])
                snapshot["quarantined"] = self._is_active_quarantine(entry, now)
                result.append(snapshot)
        return result

    # ---- Internal helpers -----------------------------------------------

    @staticmethod
    def _normalize(provider: Any) -> str:
        return str(provider or "").strip()

    @staticmethod
    def _safe_parse(iso: str) -> datetime | None:
        try:
            return datetime.fromisoformat(iso)
        except (TypeError, ValueError):
            return None

    def _is_active_quarantine(self, entry: dict[str, Any], now: datetime) -> bool:
        expires_iso = entry.get("quarantined_until")
        if not expires_iso:
            return False
        expires = self._safe_parse(str(expires_iso))
        if expires is None:
            return False
        return now < expires

    def _load_from_disk(self) -> None:
        path = self._storage_path
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "provider_quarantine_load_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        if not isinstance(raw, dict):
            logger.warning("provider_quarantine_load_malformed", path=str(path))
            return
        loaded = 0
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            self._entries[str(key)] = {
                "failures": list(value.get("failures") or []),
                "quarantined_until": value.get("quarantined_until"),
                "last_reason": value.get("last_reason"),
            }
            loaded += 1
        if loaded:
            logger.info("provider_quarantine_loaded", loaded=loaded)

    def _persist_to_disk(self) -> None:
        path = self._storage_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: tmp + replace.
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except (OSError, TypeError) as exc:
            logger.warning(
                "provider_quarantine_persist_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )


# Module-level singleton — pattern совпадает с chat_ban_cache, silence_mode.
provider_quarantine = ProviderQuarantine()
