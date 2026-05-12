# -*- coding: utf-8 -*-
"""Wave 70: KraabUserbot weakref для on-scrape collector callbacks
(dispatcher tick / swarm probes / paid Gemini guard).

Хранится weakref чтобы не удерживать userbot от GC при shutdown.
"""

from __future__ import annotations

import weakref as _weakref

_USERBOT_REF: "_weakref.ReferenceType | None" = None


def register_userbot_for_metrics(userbot: object) -> None:
    """Регистрирует KraabUserbot для Wave 70 collector callbacks.

    Повторный вызов перезаписывает. Не бросает.
    """
    global _USERBOT_REF
    try:
        _USERBOT_REF = _weakref.ref(userbot) if userbot is not None else None
    except TypeError:
        # Объект может не поддерживать weakref (mock без __weakref__).
        _USERBOT_REF = None


def _get_userbot_for_metrics() -> object | None:
    """Возвращает userbot из weakref или None."""
    ref = _USERBOT_REF
    if ref is None:
        return None
    try:
        return ref()
    except Exception:  # noqa: BLE001
        return None
