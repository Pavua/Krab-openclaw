# -*- coding: utf-8 -*-
"""
Фильтр спама/рассылок — расширенная версия.

Два уровня фильтрации:
1. is_notification_sender — SMS shortcodes (≤5 цифр)
2. is_bulk_sender — сервисные аккаунты, OTP, scam/fake
"""

from __future__ import annotations

import re

_OTP_PATTERNS = re.compile(
    r"(?i)\b(verification|otp|код|code|sms|notif|одноразов|подтвержд|рассылк|newsletter|noreply|no.?reply)\b"
)


def is_notification_sender(user: object) -> bool:
    """SMS/iMessage shortcode (≤5 цифр) — не принимают входящие."""
    username = str(getattr(user, "username", "") or "").strip().lstrip("@")
    phone = (
        str(getattr(user, "phone", "") or "").strip().lstrip("+").replace(" ", "").replace("-", "")
    )
    for candidate in (username, phone):
        if candidate and candidate.isdigit() and len(candidate) <= 5:
            return True
    return False


def is_bulk_sender(user: object) -> bool:
    """Массовые рассылки, сервисные аккаунты, OTP, scam/fake."""
    # scam/fake флаги Telegram
    if bool(getattr(user, "is_scam", False)):
        return True
    if bool(getattr(user, "is_fake", False)):
        return True

    # Verified сервисный аккаунт без username (банки, сервисы доставки)
    is_verified = bool(getattr(user, "is_verified", False))
    username = str(getattr(user, "username", "") or "").strip()
    if is_verified and not username:
        return True

    # OTP/рассылочные паттерны в first_name
    first_name = str(getattr(user, "first_name", "") or "").strip()
    if first_name and _OTP_PATTERNS.search(first_name):
        return True

    return False


def should_skip_auto_reply(user: object) -> bool:
    """Комбинированная проверка: пропустить авто-ответ?"""
    return is_notification_sender(user) or is_bulk_sender(user)
