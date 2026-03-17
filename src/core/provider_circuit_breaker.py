# -*- coding: utf-8 -*-
"""
Circuit breaker для провайдеров Krab.

Отслеживает ошибки auth (401/403) и quota (429) по провайдерам.
После FAILURE_THRESHOLD ошибок за FAILURE_WINDOW_SEC — трипает провайдера
на RECOVERY_SEC. Состояние персистируется в JSON-файл.

Не пишет в auth-profiles.json (территория OpenClaw) — только в собственный
krab_circuit_breaker.json внутри krab_runtime_state/.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

_STATE_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "provider_circuit_breaker.json"

# Thresholds
FAILURE_WINDOW_SEC: int = 300   # окно учёта ошибок, 5 минут
FAILURE_THRESHOLD: int = 3      # ошибок за окно → trip
RECOVERY_SEC: int = 1800        # cooldown после трипа, 30 минут

# Типы ошибок, которые учитываются
TRACKED_ERROR_KINDS: frozenset[str] = frozenset({"auth", "quota"})


class ProviderCircuitBreaker:
    """
    Thread-safe in-memory + file-backed circuit breaker.

    Структура state:
    {
      "provider_name": {
        "failures": [timestamp, ...],   # только внутри текущего окна
        "tripped_until": float,         # epoch when cooldown expires (0 = not tripped)
        "total_trips": int
      }
    }
    """

    def __init__(self, state_file: Path = _STATE_FILE) -> None:
        self._state_file = state_file
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def record_failure(self, provider: str, error_kind: str) -> bool:
        """
        Записывает ошибку провайдера.
        Возвращает True если circuit сработал (провайдер затриплен).
        Ошибки не из TRACKED_ERROR_KINDS игнорируются.
        """
        if error_kind not in TRACKED_ERROR_KINDS:
            return False

        provider = _normalize_provider(provider)
        if not provider:
            return False

        now = time.time()
        with self._lock:
            entry = self._state.setdefault(provider, _empty_entry())

            # Если уже затриплен — не добавляем ошибки поверх
            if entry["tripped_until"] > now:
                return True

            # Добавляем timestamp и чистим старые за пределами окна
            entry["failures"].append(now)
            cutoff = now - FAILURE_WINDOW_SEC
            entry["failures"] = [t for t in entry["failures"] if t >= cutoff]

            if len(entry["failures"]) >= FAILURE_THRESHOLD:
                entry["tripped_until"] = now + RECOVERY_SEC
                entry["total_trips"] += 1
                entry["failures"] = []  # сбрасываем счётчик после трипа
                self._save_locked()
                logger.warning(
                    "circuit_breaker_tripped",
                    provider=provider,
                    error_kind=error_kind,
                    tripped_until=entry["tripped_until"],
                    total_trips=entry["total_trips"],
                )
                return True

            self._save_locked()
            return False

    def is_tripped(self, provider: str) -> bool:
        """True если провайдер сейчас в cooldown."""
        provider = _normalize_provider(provider)
        if not provider:
            return False

        now = time.time()
        with self._lock:
            entry = self._state.get(provider)
            if not entry:
                return False
            tripped = entry["tripped_until"] > now
            if not tripped and entry["tripped_until"] > 0:
                # Cooldown истёк — сбрасываем
                entry["tripped_until"] = 0.0
                self._save_locked()
            return tripped

    def record_success(self, provider: str) -> None:
        """Сбрасывает счётчик ошибок при успешном ответе провайдера."""
        provider = _normalize_provider(provider)
        if not provider:
            return

        now = time.time()
        with self._lock:
            entry = self._state.get(provider)
            if not entry:
                return
            # Не сбрасываем активный трип — он должен отработать до конца cooldown
            if entry["tripped_until"] > now:
                return
            if entry["failures"]:
                entry["failures"] = []
                self._save_locked()

    def get_status(self) -> dict[str, Any]:
        """Возвращает снимок состояния всех провайдеров (для диагностики/UI)."""
        now = time.time()
        with self._lock:
            result: dict[str, Any] = {}
            for provider, entry in self._state.items():
                tripped = entry["tripped_until"] > now
                result[provider] = {
                    "tripped": tripped,
                    "tripped_until": entry["tripped_until"] if tripped else 0.0,
                    "tripped_until_human": _epoch_to_human(entry["tripped_until"]) if tripped else "",
                    "pending_failures": len(entry["failures"]),
                    "total_trips": entry["total_trips"],
                }
            return result

    def reset_provider(self, provider: str) -> None:
        """Принудительно сбрасывает circuit для провайдера (для диагностики/ручного override)."""
        provider = _normalize_provider(provider)
        if not provider:
            return
        with self._lock:
            if provider in self._state:
                self._state[provider] = _empty_entry()
                self._save_locked()
                logger.info("circuit_breaker_reset", provider=provider)

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._state_file.exists():
                raw = json.loads(self._state_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for provider, entry in raw.items():
                        if isinstance(entry, dict):
                            self._state[str(provider)] = {
                                "failures": [float(t) for t in entry.get("failures", []) if isinstance(t, (int, float))],
                                "tripped_until": float(entry.get("tripped_until", 0.0) or 0.0),
                                "total_trips": int(entry.get("total_trips", 0) or 0),
                            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("circuit_breaker_load_failed", error=str(exc))

    def _save_locked(self) -> None:
        """Called with self._lock held."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("circuit_breaker_save_failed", error=str(exc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_provider(provider: str) -> str:
    """Extracts provider name from 'provider/model' or returns as-is."""
    raw = str(provider or "").strip().lower()
    if "/" in raw:
        raw = raw.split("/")[0]
    return raw


def _empty_entry() -> dict[str, Any]:
    return {"failures": [], "tripped_until": 0.0, "total_trips": 0}


def _epoch_to_human(epoch: float) -> str:
    """Returns human-readable time until recovery."""
    remaining = max(0.0, epoch - time.time())
    minutes = int(remaining) // 60
    seconds = int(remaining) % 60
    return f"{minutes}м {seconds}с"


# ── Module-level singleton ────────────────────────────────────────────────────

circuit_breaker = ProviderCircuitBreaker()
