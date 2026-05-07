# -*- coding: utf-8 -*-
"""Wave 6: frame-aware narrowing CancelledError suppression в _before_send.

Раньше bare "CancelledError" marker в _BENIGN_ERROR_MARKERS глотал ВСЕ
asyncio.CancelledError — включая timeout-induced cancellations из
asyncio.wait_for(...) в LLM/MCP/memory путях. Теперь подавление только
если cancel пришёл из app-shutdown lifespan (uvicorn/starlette).
"""

from __future__ import annotations

from src.bootstrap.sentry_init import _before_send, _is_shutdown_cancelled_error


def _cancelled_event(*, transaction: str = "", frames: list | None = None) -> dict:
    """Build minimal Sentry event mimicking CancelledError shape."""
    return {
        "transaction": transaction,
        "exception": {
            "values": [
                {
                    "type": "CancelledError",
                    "value": "",
                    "stacktrace": {"frames": frames or []},
                }
            ]
        },
    }


def test_lifespan_transaction_swallowed():
    event = _cancelled_event(transaction="lifespan")
    assert _is_shutdown_cancelled_error(event, {}) is True
    assert _before_send(event, {}) is None


def test_lifespan_transaction_case_insensitive():
    event = _cancelled_event(transaction="Lifespan.shutdown")
    assert _is_shutdown_cancelled_error(event, {}) is True


def test_starlette_routing_frame_swallowed():
    frames = [
        {"abs_path": "/usr/lib/python3.13/asyncio/tasks.py"},
        {"abs_path": "/site-packages/starlette/routing.py", "function": "lifespan"},
    ]
    event = _cancelled_event(frames=frames)
    assert _is_shutdown_cancelled_error(event, {}) is True
    assert _before_send(event, {}) is None


def test_uvicorn_lifespan_frame_swallowed():
    frames = [
        {"abs_path": "/usr/lib/python3.13/asyncio/tasks.py"},
        {"abs_path": "/site-packages/uvicorn/lifespan/on.py", "function": "main"},
    ]
    event = _cancelled_event(frames=frames)
    assert _is_shutdown_cancelled_error(event, {}) is True
    assert _before_send(event, {}) is None


def test_random_cancelled_error_propagates():
    """Critical case: timeout-induced cancel в user-code должен дойти до Sentry."""
    frames = [
        {"abs_path": "/app/src/openclaw_client.py", "function": "call_with_timeout"},
        {"abs_path": "/usr/lib/python3.13/asyncio/tasks.py", "function": "wait_for"},
        {"abs_path": "/app/src/userbot_bridge.py", "function": "_handle_message"},
    ]
    event = _cancelled_event(transaction="POST /api/assistant/query", frames=frames)
    assert _is_shutdown_cancelled_error(event, {}) is False
    # Event должно пройти through, не None
    assert _before_send(event, {}) is event


def test_malformed_event_doesnt_crash():
    # Полностью пустое
    assert _is_shutdown_cancelled_error({}, {}) is False
    # exception=None
    assert _is_shutdown_cancelled_error({"exception": None}, {}) is False
    # values=None
    assert _is_shutdown_cancelled_error({"exception": {"values": None}}, {}) is False
    # frames=string instead of list
    bad = {
        "exception": {"values": [{"stacktrace": {"frames": "not-a-list"}}]},
    }
    assert _is_shutdown_cancelled_error(bad, {}) is False
    # frame не dict
    bad2 = {
        "exception": {"values": [{"stacktrace": {"frames": ["not-a-dict"]}}]},
    }
    assert _is_shutdown_cancelled_error(bad2, {}) is False
    # transaction не string
    bad3 = {"transaction": 12345}
    assert _is_shutdown_cancelled_error(bad3, {}) is False


def test_top_5_frames_only():
    """Older frames (deeper than top-5) не должны давать ложного срабатывания."""
    # 10 user-code frames + 1 lifespan-frame в самом начале (oldest)
    frames = [{"abs_path": "/site-packages/starlette/routing.py"}]
    frames += [{"abs_path": f"/app/src/module_{i}.py", "function": f"f{i}"} for i in range(10)]
    event = _cancelled_event(frames=frames)
    # Top-5 — все user-code, lifespan-frame глубоко в стеке → False
    assert _is_shutdown_cancelled_error(event, {}) is False


def test_filename_field_fallback():
    """Frame без abs_path, но с filename — тоже учитывается."""
    frames = [{"filename": "/site-packages/uvicorn/lifespan/on.py"}]
    event = _cancelled_event(frames=frames)
    assert _is_shutdown_cancelled_error(event, {}) is True


def test_non_cancelled_error_not_affected():
    """Гарантия что обычные ошибки не задеваются новой логикой."""
    event = {
        "transaction": "lifespan",  # даже с lifespan
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "totally legit bug",
                    "stacktrace": {"frames": []},
                }
            ]
        },
    }
    # _is_shutdown_cancelled_error вернёт True из-за transaction, но _before_send
    # вызывает его только для CancelledError → ValueError проходит.
    assert _before_send(event, {}) is event


def test_other_benign_markers_still_work():
    """Регрессия: остальные benign-маркеры (userbot_not_ready и т.д.) не сломаны."""
    event = {
        "exception": {"values": [{"type": "HTTPException", "value": "503: userbot_not_ready"}]}
    }
    assert _before_send(event, {}) is None


# Session 39: PYTHON-FASTAPI-Z (387 events за 14 дней) — uvicorn.error logger
# ловил CancelledError в LifespanOn.main, но фильтр не срабатывал, потому что
# logger.error events не имеют frames в exception блоке.


def test_uvicorn_error_logger_cancelled_is_shutdown():
    """logger=uvicorn.error → shutdown lifespan, suppress."""
    event = {
        "logger": "uvicorn.error",
        "exception": {"values": [{"type": "CancelledError", "value": ""}]},
    }
    assert _is_shutdown_cancelled_error(event) is True
    assert _before_send(event, {}) is None


def test_uvicorn_lifespan_logger_cancelled_is_shutdown():
    """logger=uvicorn.lifespan тоже shutdown-related."""
    event = {
        "logger": "uvicorn.lifespan.on",
        "exception": {"values": [{"type": "CancelledError", "value": ""}]},
    }
    assert _is_shutdown_cancelled_error(event) is True


def test_trace_description_lifespan_is_shutdown():
    """contexts.trace.description содержит 'LifespanOn.main' → shutdown."""
    event = {
        "contexts": {"trace": {"description": "LifespanOn.main"}},
        "exception": {"values": [{"type": "CancelledError", "value": ""}]},
    }
    assert _is_shutdown_cancelled_error(event) is True


def test_threads_frames_lifespan_is_shutdown():
    """Sentry может класть stacktrace в threads.values, не в exception.values."""
    event = {
        "threads": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"filename": "/app/main.py", "function": "main"},
                            {"filename": "/lib/uvicorn/lifespan/on.py", "function": "receive"},
                        ]
                    }
                }
            ]
        },
        "exception": {"values": [{"type": "CancelledError", "value": ""}]},
    }
    assert _is_shutdown_cancelled_error(event) is True


def test_random_logger_cancelled_not_shutdown():
    """logger=src.openclaw_client (LLM timeout) → НЕ shutdown, пропускаем."""
    event = {
        "logger": "src.openclaw_client",
        "exception": {"values": [{"type": "CancelledError", "value": "wait_for timeout"}]},
    }
    assert _is_shutdown_cancelled_error(event) is False
    # _before_send → продолжает, не глотает (no benign marker matches)
    assert _before_send(event, {}) is event
