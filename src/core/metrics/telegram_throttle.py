# -*- coding: utf-8 -*-
"""Wave 127: pre-emptive Telegram outgoing throttle metrics.

Gauge `krab_telegram_outgoing_rate_per_sec{caller}` — rolling rate в окне 10s.
Counter `krab_telegram_throttle_applied_total{caller}` — сколько раз
pre-emptive delay сработал для caller.

Best-effort: prometheus_client опционален; функции не бросают.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterTH  # type: ignore[import-not-found]
    from prometheus_client import Gauge as _GaugeTH  # type: ignore[import-not-found]

    _outgoing_rate_per_sec = _GaugeTH(
        "krab_telegram_outgoing_rate_per_sec",
        "Wave 127: исходящий rate (msg/sec) per caller в скользящем окне 10s",
        ["caller"],
    )
    _throttle_applied_total = _CounterTH(
        "krab_telegram_throttle_applied_total",
        "Wave 127: count of pre-emptive throttle delays per caller",
        ["caller"],
    )
except Exception:  # noqa: BLE001 - prometheus_client optional
    _outgoing_rate_per_sec = None  # type: ignore[assignment]
    _throttle_applied_total = None  # type: ignore[assignment]


def set_outgoing_rate(caller: str, rate: float) -> None:
    """Wave 127: обновляет Gauge текущего rate per caller."""
    try:
        if _outgoing_rate_per_sec is None:
            return
        clean_caller = (str(caller) or "unknown")[:80]
        _outgoing_rate_per_sec.labels(caller=clean_caller).set(max(0.0, float(rate)))
    except Exception:  # noqa: BLE001
        pass


def inc_throttle_applied(caller: str) -> None:
    """Wave 127: инкремент counter применения pre-emptive throttle."""
    try:
        if _throttle_applied_total is None:
            return
        clean_caller = (str(caller) or "unknown")[:80]
        _throttle_applied_total.labels(caller=clean_caller).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "_outgoing_rate_per_sec",
    "_throttle_applied_total",
    "inc_throttle_applied",
    "set_outgoing_rate",
]
