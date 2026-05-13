# -*- coding: utf-8 -*-
"""Wave 245: OpenClaw bypass recommender — Sentry suggestion на high fail rate.

Назначение
----------
Отслеживает success/fail rate gateway вызовов в скользящем окне (1 час).
При fail_rate > 50% и достаточно samples — однократно отправляет Sentry
warning с тегом ``recommend=enable_KRAB_OPENCLAW_BYPASS_ENABLED``,
чтобы оператор получил подсказку включить bypass.

API
---
- ``record_openclaw_outcome(success: bool)`` — вызывать после каждого
  send_message_stream/health probe.
- ``should_recommend_bypass()`` — возвращает True если пора предупреждать
  (учитывает quiet-period 1h после прошлого alert).

Хранилище — in-memory (deque + locks). Не персистится — после рестарта
счёт обнуляется.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from .logger import get_logger

logger = get_logger(__name__)

# Окно агрегации (секунды).
_WINDOW_SEC = 3600.0
# Минимум samples для оценки fail-rate (иначе слишком шумно).
_MIN_SAMPLES = 5
# Порог fail-rate (0.0..1.0).
_FAIL_RATE_THRESHOLD = 0.5
# Quiet-period после Sentry alert (секунды).
_QUIET_AFTER_ALERT_SEC = 3600.0

# Каждая запись: (timestamp_monotonic, success_bool).
_events: deque[tuple[float, bool]] = deque()
_events_lock = threading.Lock()

# Время последнего отправленного Sentry alert. None — ещё не отправляли.
_last_alert_ts: float | None = None
_alert_lock = threading.Lock()


def _bypass_already_on() -> bool:
    """Не предлагаем bypass если он уже включён."""
    return str(os.environ.get("KRAB_OPENCLAW_BYPASS_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def record_openclaw_outcome(success: bool) -> None:
    """Зарегистрировать исход одного OpenClaw-вызова.

    Аргумент: True если запрос прошёл успешно, False — сломался.
    """
    now = time.monotonic()
    with _events_lock:
        _events.append((now, bool(success)))
        # Чистим старое (за пределами окна).
        cutoff = now - _WINDOW_SEC
        while _events and _events[0][0] < cutoff:
            _events.popleft()


def _fail_rate_snapshot() -> tuple[int, float]:
    """Возвращает (total_samples, fail_rate). fail_rate в [0..1]."""
    with _events_lock:
        total = len(_events)
        if total == 0:
            return 0, 0.0
        fails = sum(1 for _, ok in _events if not ok)
        return total, fails / total


def should_recommend_bypass() -> bool:
    """True если стоит порекомендовать оператору включить bypass.

    Учитывает:
    - bypass ещё не включён (иначе нет смысла рекомендовать);
    - набралось >= _MIN_SAMPLES;
    - fail_rate >= _FAIL_RATE_THRESHOLD;
    - с прошлого alert прошёл quiet-period.
    """
    if _bypass_already_on():
        return False
    total, fail_rate = _fail_rate_snapshot()
    if total < _MIN_SAMPLES:
        return False
    if fail_rate < _FAIL_RATE_THRESHOLD:
        return False
    with _alert_lock:
        now = time.monotonic()
        if _last_alert_ts is not None and (now - _last_alert_ts) < _QUIET_AFTER_ALERT_SEC:
            return False
        return True


def mark_alert_sent() -> None:
    """Помечает, что Sentry alert уже отправлен (rate-limit)."""
    global _last_alert_ts
    with _alert_lock:
        _last_alert_ts = time.monotonic()


def maybe_send_bypass_recommendation() -> bool:
    """Если пора — отправляет Sentry warning. Возвращает True если послали.

    Использует sentry_sdk при доступности. Graceful no-op без sentry_sdk.
    """
    if not should_recommend_bypass():
        return False

    total, fail_rate = _fail_rate_snapshot()
    payload = {
        "samples_last_hour": total,
        "fail_rate": round(fail_rate, 3),
        "recommend": "enable_KRAB_OPENCLAW_BYPASS_ENABLED",
        "doc": "docs/OPENCLAW_BYPASS_GUIDE.md",
    }
    logger.warning("openclaw_bypass_recommended", **payload)

    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            scope.set_tag("recommend", "enable_KRAB_OPENCLAW_BYPASS_ENABLED")
            scope.set_tag("openclaw_health", "degraded")
            scope.set_extra("samples_last_hour", total)
            scope.set_extra("fail_rate", round(fail_rate, 3))
            sentry_sdk.capture_message(
                "OpenClaw gateway fail rate > 50% — рекомендуется включить "
                "KRAB_OPENCLAW_BYPASS_ENABLED=1 для аварийного direct-routing",
                level="warning",
            )
    except Exception as exc:  # noqa: BLE001
        # Sentry недоступен — это OK, мы уже залогировали через structlog.
        logger.debug("openclaw_bypass_recommend_sentry_failed", error=str(exc))

    mark_alert_sent()
    return True


def _reset_state_for_tests() -> None:
    """Только для тестов: чистит deque + alert quiet-period."""
    global _last_alert_ts
    with _events_lock:
        _events.clear()
    with _alert_lock:
        _last_alert_ts = None


__all__ = [
    "record_openclaw_outcome",
    "should_recommend_bypass",
    "maybe_send_bypass_recommendation",
    "mark_alert_sent",
    "_reset_state_for_tests",
]
