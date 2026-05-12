# -*- coding: utf-8 -*-
"""Prometheus-метрики Wave 108 moderation audit log.

Counter `krab_moderation_actions_total{action}` инкрементируется на каждый
успешный `log_action`. Если prometheus_client недоступен — no-op.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]

    krab_moderation_actions_total: Any = _Counter(
        "krab_moderation_actions_total",
        "Модерационные действия Krab (ban/unban/mute и т.п.) с разбивкой по action",
        ["action"],
    )
except Exception:  # noqa: BLE001
    krab_moderation_actions_total = None


def record_action(action: str) -> None:
    """Инкрементирует counter (no-op если prometheus недоступен)."""
    if krab_moderation_actions_total is None:
        return
    try:
        krab_moderation_actions_total.labels(action=action or "unknown").inc()
    except Exception:  # noqa: BLE001
        pass
