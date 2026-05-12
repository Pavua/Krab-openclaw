# -*- coding: utf-8 -*-
"""Wave 142: Pyrogram reconnect storm detection.

Counter krab_pyrogram_disconnects_total{session} инкрементируется
PyrogramReconnectMetricFilter каждый раз когда видит "Disconnected" log
от pyrogram.connection.connection.

Used by alert PyrogramReconnectStorm: rate > 0.1/sec for 5m warning.
"""

from __future__ import annotations

try:
    from prometheus_client import Counter as _CounterPR  # type: ignore[import-not-found]

    _pyrogram_disconnects_total = _CounterPR(
        "krab_pyrogram_disconnects_total",
        "Wave 142: Pyrogram Connection.close events (Disconnected log lines) by session",
        ["session"],
    )
except Exception:  # noqa: BLE001
    _pyrogram_disconnects_total = None  # type: ignore[assignment]


_PYROGRAM_DISCONNECTS_COUNTER: dict[str, int] = {}
_PYROGRAM_SESSION_LABEL: list[str] = ["unknown"]


def set_pyrogram_session_label(session_name: str) -> None:
    """Wave 142: set session label для последующих inc_pyrogram_disconnect."""
    try:
        label = str(session_name or "unknown").strip()[:60] or "unknown"
        _PYROGRAM_SESSION_LABEL[0] = label
    except Exception:  # noqa: BLE001
        pass


def get_pyrogram_session_label() -> str:
    """Wave 142: текущий session label (для тестов)."""
    return _PYROGRAM_SESSION_LABEL[0]


def inc_pyrogram_disconnect(session: str | None = None) -> None:
    """Wave 142: инкрементирует Pyrogram Disconnect counter. Best-effort."""
    try:
        label = session if session is not None else _PYROGRAM_SESSION_LABEL[0]
        key = str(label or "unknown")[:60] or "unknown"
        _PYROGRAM_DISCONNECTS_COUNTER[key] = _PYROGRAM_DISCONNECTS_COUNTER.get(key, 0) + 1
        if _pyrogram_disconnects_total is not None:
            _pyrogram_disconnects_total.labels(session=key).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "_PYROGRAM_DISCONNECTS_COUNTER",
    "_PYROGRAM_SESSION_LABEL",
    "_pyrogram_disconnects_total",
    "get_pyrogram_session_label",
    "inc_pyrogram_disconnect",
    "set_pyrogram_session_label",
]
