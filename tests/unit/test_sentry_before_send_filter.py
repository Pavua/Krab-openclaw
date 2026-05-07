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


# ── Session 33 Wave 6: CancelledError frame-aware narrowing ──
# Раньше ВСЕ CancelledError глотались. Теперь — только те что пришли из
# uvicorn/starlette lifespan teardown. Подробные тесты в
# test_sentry_cancelled_narrow.py; здесь только smoke regression-checks.


def test_before_send_drops_cancelled_error_in_lifespan_transaction() -> None:
    """CancelledError с transaction=lifespan → подавляется (shutdown noise)."""
    event = {
        "transaction": "lifespan",
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": "",
                },
            ]
        },
    }
    assert _before_send(event, {}) is None


def test_before_send_keeps_cancelled_error_outside_lifespan() -> None:
    """CancelledError без lifespan-маркеров → пропускается в Sentry.

    Это критичный кейс: timeout-induced cancel из asyncio.wait_for(...) в
    LLM/MCP/memory путях должен быть видимым, а не silent-dropped.
    """
    event = {
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": "",
                },
            ]
        }
    }
    assert _before_send(event, {}) is event


def test_before_send_drops_cancelled_error_with_starlette_frame() -> None:
    """CancelledError с frame в starlette/routing → подавляется."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "asyncio.exceptions.CancelledError",
                    "value": "",
                    "stacktrace": {
                        "frames": [
                            {"abs_path": "/site-packages/starlette/routing.py"},
                        ]
                    },
                },
            ]
        }
    }
    assert _before_send(event, {}) is None


# ---------------------------------------------------------------------------
# Session 40: pytest event pollution filter (PYTHON-FASTAPI-83/84/85/63)
# ---------------------------------------------------------------------------

from src.bootstrap.sentry_init import _is_pytest_event  # noqa: E402


def test_pytest_filter_drops_testserver_url() -> None:
    """FastAPI TestClient prepends http://testserver/ — это маркер test event."""
    event = {
        "request": {
            "url": "http://testserver/api/context/checkpoint",
            "method": "POST",
        },
        "exception": {
            "values": [
                {
                    "type": "HTTPException",
                    "value": "context_checkpoint_failed:boom",
                }
            ]
        },
    }
    assert _is_pytest_event(event) is True
    assert _before_send(event, {}) is None


def test_pytest_filter_drops_pytest_of_path_in_extra() -> None:
    """db_corruption_detected с db_path в pytest-of- → drop."""
    event = {
        "extra": {
            "db_path": (
                "/private/var/folders/vv/.../pytest-of-pablito/pytest-150/"
                "popen-gw2/test_preflight_recent_backup_r0/kraab.session"
            ),
            "detail": "database disk image is malformed",
        },
        "message": "db_corruption_detected: session",
    }
    assert _is_pytest_event(event) is True
    assert _before_send(event, {}) is None


def test_pytest_filter_drops_xdist_worker_argv() -> None:
    """sys.argv == ['-c'] — xdist worker subprocess (`python -c '...'`)."""
    event = {
        "extra": {"sys.argv": ["-c"]},
        "exception": {
            "values": [{"type": "RuntimeError", "value": "anything"}]
        },
    }
    assert _is_pytest_event(event) is True
    assert _before_send(event, {}) is None


def test_pytest_filter_drops_popen_gw_path() -> None:
    """Любое extra-поле с popen-gw → xdist worker tmp dir → drop."""
    event = {
        "extra": {
            "log_path": "/tmp/pytest-of-user/pytest-1/popen-gw5/some.log",
        },
    }
    assert _is_pytest_event(event) is True
    assert _before_send(event, {}) is None


def test_pytest_filter_handles_malformed_event_gracefully() -> None:
    """Defensive: даже на странном shape (None values) не падаем."""
    assert _is_pytest_event({}) is False
    assert _is_pytest_event({"request": None}) is False
    assert _is_pytest_event({"extra": None}) is False
    assert _is_pytest_event({"request": "not_a_dict"}) is False


def test_pytest_filter_passes_real_production_event() -> None:
    """Производственный event без pytest-маркеров → не дропается."""
    event = {
        "request": {
            "url": "https://krab.production.com/api/health",
            "method": "GET",
        },
        "extra": {"error_code": "real_production_bug"},
        "exception": {
            "values": [{"type": "ValueError", "value": "real bug"}]
        },
    }
    assert _is_pytest_event(event) is False
    # Передаётся дальше (не None)
    assert _before_send(event, {}) is event


def test_pytest_filter_unknown_argv_not_filtered() -> None:
    """sys.argv = ['python', 'manage.py', ...] — не pytest, не дропать."""
    event = {
        "extra": {"sys.argv": ["python", "manage.py", "runserver"]},
        "exception": {
            "values": [{"type": "RuntimeError", "value": "real bug"}]
        },
    }
    assert _is_pytest_event(event) is False
