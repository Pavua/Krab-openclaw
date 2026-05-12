# -*- coding: utf-8 -*-
"""
src/core/metrics/catchup.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wave 116: Prometheus метрики для startup catchup (Wave 46-A / 48-A).

- Counter `krab_catchup_message_processed_total{chat_id, status}`
  status ∈ {processed, skipped, error}
- Histogram `krab_catchup_age_seconds{chat_id}` — возраст сообщения (sec)
  в момент replay'а.
- Gauge `krab_startup_catchup_completed_ts` — unix-timestamp последнего
  успешного завершения multi-chat catchup'а.
- Counter `krab_startup_catchup_failures_total{stage}` — общие фейлы
  catchup (stage: fetch/replay/unexpected).

Fail-safe: prometheus_client опционален — при отсутствии helpers no-op.
"""

from __future__ import annotations

from ..logger import get_logger

logger = get_logger(__name__)


try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_catchup_message_processed_total = _Counter(
        "krab_catchup_message_processed_total",
        "Сообщения, обработанные на этапе startup catchup",
        ["chat_id", "status"],
    )
    krab_catchup_age_seconds = _Histogram(
        "krab_catchup_age_seconds",
        "Возраст (секунды) catchup-сообщения в момент replay'а",
        ["chat_id"],
        # Buckets: 1s свежие, 30s/2m/10m средние, 1h/6h/24h+ stale.
        buckets=(1.0, 5.0, 30.0, 120.0, 600.0, 3600.0, 21600.0, 86400.0),
    )
    krab_startup_catchup_completed_ts = _Gauge(
        "krab_startup_catchup_completed_ts",
        "Unix timestamp последнего успешного multi-chat catchup",
    )
    krab_startup_catchup_failures_total = _Counter(
        "krab_startup_catchup_failures_total",
        "Фейлы startup catchup по этапу",
        ["stage"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    krab_catchup_message_processed_total = None  # type: ignore[assignment]
    krab_catchup_age_seconds = None  # type: ignore[assignment]
    krab_startup_catchup_completed_ts = None  # type: ignore[assignment]
    krab_startup_catchup_failures_total = None  # type: ignore[assignment]


def _safe_chat_label(chat_id: int | str) -> str:
    """Кардинальность ограничена самим набором target chats (≤10)."""
    try:
        return str(int(chat_id))
    except (TypeError, ValueError):
        return "unknown"


def record_catchup_message(chat_id: int | str, status: str) -> None:
    """Инкремент Counter для одного catchup-сообщения.

    status ∈ {processed, skipped, error}.
    """
    try:
        if krab_catchup_message_processed_total is None:
            return
        s = (status or "processed")[:32]
        krab_catchup_message_processed_total.labels(
            chat_id=_safe_chat_label(chat_id), status=s
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_catchup_age(chat_id: int | str, age_seconds: float) -> None:
    """Histogram observation возраста сообщения. Negative clamp к 0."""
    try:
        if krab_catchup_age_seconds is None:
            return
        v = max(0.0, float(age_seconds))
        krab_catchup_age_seconds.labels(chat_id=_safe_chat_label(chat_id)).observe(v)
    except Exception:  # noqa: BLE001
        pass


def mark_catchup_completed(ts: float) -> None:
    """Gauge set при успешном завершении multi-chat catchup'а."""
    try:
        if krab_startup_catchup_completed_ts is None:
            return
        krab_startup_catchup_completed_ts.set(float(ts))
    except Exception:  # noqa: BLE001
        pass


def record_catchup_failure(stage: str) -> None:
    """Counter инкремент общих failures.

    stage ∈ {fetch, replay, unexpected, chat}.
    """
    try:
        if krab_startup_catchup_failures_total is None:
            return
        s = (stage or "unexpected")[:32]
        krab_startup_catchup_failures_total.labels(stage=s).inc()
    except Exception:  # noqa: BLE001
        pass
