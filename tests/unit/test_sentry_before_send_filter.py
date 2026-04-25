# -*- coding: utf-8 -*-
"""
Тесты Sentry before_send-фильтра (bootstrap/sentry_init.py).

Цель: drop benign-события вроде userbot_not_ready (503 во время Krab boot),
чтобы они не спамили Sentry — это transient, не actionable bug.
"""

from __future__ import annotations

from src.bootstrap.sentry_init import _before_send


def test_before_send_drops_userbot_not_ready_via_extra() -> None:
    """Событие с extra.error_code == 'userbot_not_ready' → None (drop)."""
    event = {"extra": {"error_code": "userbot_not_ready"}}
    assert _before_send(event, {}) is None


def test_before_send_drops_userbot_not_ready_in_exception_value() -> None:
    """HTTPException(503, 'userbot_not_ready') → exception.value содержит маркер."""
    event = {
        "exception": {
            "values": [
                {"type": "HTTPException", "value": "503: userbot_not_ready"},
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_userbot_not_ready_in_message() -> None:
    """logging.warning('userbot_not_ready ...') → message содержит маркер."""
    event = {"message": "userbot_not_ready during boot"}
    assert _before_send(event, {}) is None


def test_before_send_keeps_unrelated_events() -> None:
    """Обычные ошибки должны проходить (не None)."""
    event = {
        "message": "some real error",
        "exception": {"values": [{"value": "ZeroDivisionError"}]},
    }
    result = _before_send(event, {})
    assert result is event


def test_before_send_safe_on_malformed_event() -> None:
    """Если event имеет странную структуру — не падаем, возвращаем как есть."""
    event = {"exception": "not-a-dict", "extra": None}
    result = _before_send(event, {})
    # malformed event не должен вызывать exception; возвращаем event
    assert result is event or result is None
