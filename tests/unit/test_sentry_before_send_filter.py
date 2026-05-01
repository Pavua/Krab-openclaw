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


# ── Session 24: расширение markers (router_not_configured + Client has not been started yet) ──


def test_before_send_drops_router_not_configured() -> None:
    """HTTPException router_not_configured → транзитное событие в boot, drop."""
    event = {
        "exception": {
            "values": [
                {"type": "HTTPException", "value": "503: router_not_configured"},
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_pyrogram_client_not_started() -> None:
    """Pyrogram 'Client has not been started yet' — race во время boot, drop."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "ConnectionError",
                    "value": "Client has not been started yet",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_router_not_configured_in_message() -> None:
    """structlog: 'router_not_configured' через message field тоже drop."""
    event = {"message": "503: router_not_configured at endpoint /api/health"}
    assert _before_send(event, {}) is None


def test_before_send_router_not_configured_via_extra_error_code() -> None:
    """extra.error_code='router_not_configured' → drop."""
    event = {"extra": {"error_code": "router_not_configured"}}
    assert _before_send(event, {}) is None


# ── Session 28: USER_BANNED + slowmode + pyrogram NoneType.to_bytes race ──


def test_before_send_drops_user_banned_in_channel_exception() -> None:
    """Pyrogram UserBannedInChannel — chat_ban_cache уже обрабатывает, Sentry не нужен."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "UserBannedInChannel",
                    "value": "Telegram says: [400 USER_BANNED_IN_CHANNEL]",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_user_banned_message_marker() -> None:
    """logger.error со строкой USER_BANNED_IN_CHANNEL → drop."""
    event = {"message": "background_ai_request_failed: USER_BANNED_IN_CHANNEL"}
    assert _before_send(event, {}) is None


def test_before_send_drops_chat_write_forbidden() -> None:
    """ChatWriteForbidden — slowmode/permissions, не runtime bug."""
    event = {
        "exception": {
            "values": [
                {"type": "ChatWriteForbidden", "value": "ChatWriteForbidden"},
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_slowmode_limited_message() -> None:
    """SlowmodeWait / 'You are limited from sending messages' — temporary, drop."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "BadRequest",
                    "value": "You are limited from sending messages for 30 seconds",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_pyrogram_nonetype_to_bytes_race() -> None:
    """Pyrogram race: AttributeError 'NoneType' object has no attribute 'to_bytes'.

    Происходит при rapid restart_userbot когда внутренний Session-task возвращает
    None в storage layer. Не runtime bug — pyrogram recovery'ится через retry.
    """
    event = {
        "exception": {
            "values": [
                {
                    "type": "AttributeError",
                    "value": "'NoneType' object has no attribute 'to_bytes'",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_keeps_real_attribute_error() -> None:
    """Обычный AttributeError (не to_bytes race) должен проходить."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "AttributeError",
                    "value": "'NoneType' object has no attribute 'username'",
                },
            ]
        }
    }
    assert _before_send(event, {}) is event


# ── Session 32: CancelledError fix — маркер в ex["type"], не ex["value"] ──


def test_before_send_drops_cancelled_error_via_type_field() -> None:
    """asyncio.CancelledError: str(exc) == '' (пустая строка), маркер только в ex['type'].

    Это корневая причина того, что 357+ событий PYTHON-FASTAPI-Z проходили в Sentry
    несмотря на маркер "CancelledError" в _BENIGN_ERROR_MARKERS: фильтр проверял только
    ex['value'] (= ''), но не ex['type'] (= 'CancelledError').
    """
    event = {
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": "",  # asyncio.CancelledError всегда даёт пустой str()
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_cancelled_error_with_module_prefix() -> None:
    """Sentry может выдавать полный qualified name типа из модуля asyncio.exceptions."""
    # Некоторые версии Sentry SDK могут ставить ex['type'] как 'asyncio.exceptions.CancelledError'
    # Маркер 'CancelledError' — подстрока, поэтому тоже должна совпадать.
    event = {
        "exception": {
            "values": [
                {
                    "type": "asyncio.exceptions.CancelledError",
                    "value": "",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_before_send_drops_cancelled_error_with_value_too() -> None:
    """CancelledError с непустым value (edge case) тоже должен дропаться."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": "Task was cancelled",
                },
            ]
        }
    }
    assert _before_send(event, {}) is None
