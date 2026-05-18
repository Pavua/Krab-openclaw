# -*- coding: utf-8 -*-
"""S69 W4: Prometheus counter for dispatcher_groups_barrier_* events.

S68 W1 ввёл barrier между `add_handler` (fire-and-forget) и `client.start()`
чтобы Pyrogram-dispatcher успел зарегистрировать handlers до того, как
начнёт приходить трафик. Этот модуль фиксирует исход barrier как Prometheus
counter для алертинга и графиков.

- ``outcome="passed"`` — barrier увидел `>= KRAB_HANDLER_BARRIER_MIN_COUNT`
  handlers до истечения `KRAB_HANDLER_BARRIER_TIMEOUT_SEC`.
- ``outcome="timeout"`` — barrier истёк timeout, прошли с degraded состоянием.

Pattern: ``idle_skip.py`` (S62 W6) — prometheus_client optional, in-memory
dict для render fallback в ``collect.py``. Helper никогда не бросает.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterDB  # type: ignore[import-not-found]

    _dispatcher_groups_barrier_total = _CounterDB(
        "krab_dispatcher_groups_barrier_total",
        "S69 W4: outcome of S68 W1 dispatcher add_handler barrier",
        ["outcome"],
    )
except Exception:  # noqa: BLE001 — prometheus_client optional
    _dispatcher_groups_barrier_total = None  # type: ignore[assignment]


# In-memory счётчик для text render fallback / тестов.
_DISPATCHER_BARRIER_COUNTER: dict[str, int] = {}


def inc_dispatcher_barrier(outcome: str) -> None:
    """S69 W4: фиксирует исход dispatcher barrier. Best-effort.

    ``outcome`` — одно из: ``passed`` / ``timeout``.
    """
    try:
        key = str(outcome) if outcome else "unknown"
        _DISPATCHER_BARRIER_COUNTER[key] = _DISPATCHER_BARRIER_COUNTER.get(key, 0) + 1
        if _dispatcher_groups_barrier_total is not None:
            _dispatcher_groups_barrier_total.labels(outcome=key).inc()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — инструментация best-effort
        pass


__all__ = [
    "_DISPATCHER_BARRIER_COUNTER",
    "_dispatcher_groups_barrier_total",
    "inc_dispatcher_barrier",
]
