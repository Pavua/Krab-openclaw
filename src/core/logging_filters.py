"""
Фильтры для stdlib logging.

PyrogramDeprecatedFilter подавляет шумные DeprecationWarning от pyrofork
2.3.69 про message.forward_from / forward_sender_name / forward_from_chat —
эти атрибуты остались для backward compat, но pyrogram пишет warning на
каждое полученное сообщение, что засоряет krab_launchd.out.log.

Активируется через KRAB_PYROGRAM_DEPR_FILTER_ENABLED (default ON).
"""

from __future__ import annotations

import logging
import os
import re

# Регексп подбирает классические формулировки pyrofork:
#   "message.forward_from is deprecated, use forward_origin instead"
#   "message.forward_from_chat property is deprecated..."
#   "message.forward_sender_name is deprecated..."
_PYROGRAM_DEPR_PATTERN = re.compile(
    r"(forward_from|forward_sender_name|forward_from_chat)\b.*?\b(is|property is)\s+deprecated",
    re.IGNORECASE,
)


class PyrogramDeprecatedFilter(logging.Filter):
    """Дропает pyrogram deprecation warnings про forward_* поля."""

    def __init__(self, name: str = "") -> None:
        super().__init__(name)

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: record args broken
            return True
        if _PYROGRAM_DEPR_PATTERN.search(message):
            return False
        return True


def is_pyrogram_depr_filter_enabled() -> bool:
    """Env-gate: дефолт ON, отключается явным '0'/'false'/'no'."""
    raw = os.environ.get("KRAB_PYROGRAM_DEPR_FILTER_ENABLED", "1")
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def install_pyrogram_depr_filter() -> PyrogramDeprecatedFilter | None:
    """
    Навешивает фильтр на logger 'pyrogram' (и его дочерние).

    Возвращает установленный фильтр или None, если отключён env-флагом.
    """
    if not is_pyrogram_depr_filter_enabled():
        return None
    flt = PyrogramDeprecatedFilter()
    logging.getLogger("pyrogram").addFilter(flt)
    return flt


__all__ = [
    "PyrogramDeprecatedFilter",
    "install_pyrogram_depr_filter",
    "is_pyrogram_depr_filter_enabled",
]
