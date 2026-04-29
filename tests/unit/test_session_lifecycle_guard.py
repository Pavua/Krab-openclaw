# -*- coding: utf-8 -*-
"""
Тесты session lifecycle guard для pyrogram storage.

Проблема: внутренние pyrogram-задачи (Session.restart/dispatcher) добегают
до storage уже после `client.stop()` и валятся на
`sqlite3.ProgrammingError: Cannot operate on a closed database` (~130
событий/сутки в Sentry, PYTHON-FASTAPI-1).

Guard `_arm_storage_shutdown_guard()` помечает storage как closed и
подменяет `_get` / `update_peers` на безопасные no-op'ы.
"""

from __future__ import annotations

import asyncio
import sqlite3
import types
from unittest.mock import MagicMock

import pytest

from src.bootstrap.sentry_init import _before_send
from src.userbot.session import SessionMixin


def _make_mixin_with_fake_client() -> tuple[SessionMixin, MagicMock]:
    """Собираем минимальный SessionMixin с fake client + storage."""
    mixin = SessionMixin.__new__(SessionMixin)

    storage = types.SimpleNamespace()
    # эмулируем sync `_get` и async `update_peers`
    storage._get = MagicMock(return_value="real_value")

    async def _real_update_peers(peers):
        return ("real_update", peers)

    storage.update_peers = _real_update_peers

    client = MagicMock()
    client.storage = storage

    mixin.client = client  # type: ignore[attr-defined]
    return mixin, client


def test_storage_guard_makes_get_safe_after_close() -> None:
    """После guard вызов `_get` не падает и возвращает None (suppressed)."""
    mixin, client = _make_mixin_with_fake_client()
    mixin._arm_storage_shutdown_guard()

    storage = client.storage
    # storage помечен как closed, _get теперь безопасен
    assert getattr(storage, "_krab_storage_closed", False) is True
    result = storage._get("peer_id")
    assert result is None


def test_storage_guard_swallows_closed_db_programming_error() -> None:
    """Если оригинальный `_get` бросает 'closed database' — guard глотает."""
    mixin, client = _make_mixin_with_fake_client()
    storage = client.storage

    def _raising_get(*_a, **_kw):
        raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    storage._get = _raising_get  # type: ignore[assignment]
    mixin._arm_storage_shutdown_guard()

    # сбросим shutdown-флаг чтобы дойти до original_get
    setattr(storage, "_krab_storage_closed", False)
    # но guard всё равно ловит ProgrammingError closed database
    result = storage._get("peer_id")
    assert result is None


def test_storage_guard_update_peers_suppressed_after_close() -> None:
    """Async `update_peers` после guard возвращает None и не трогает БД."""
    mixin, client = _make_mixin_with_fake_client()
    mixin._arm_storage_shutdown_guard()

    storage = client.storage
    result = asyncio.run(storage.update_peers([("peer", 1)]))
    assert result is None


def test_storage_guard_double_arm_is_idempotent() -> None:
    """Повторный _arm_storage_shutdown_guard не ломает state и не дублирует."""
    mixin, client = _make_mixin_with_fake_client()
    mixin._arm_storage_shutdown_guard()
    storage = client.storage
    first_get = storage._get

    mixin._arm_storage_shutdown_guard()
    # ссылка на guarded `_get` не должна измениться (idempotent)
    assert storage._get is first_get
    assert getattr(storage, "_krab_storage_closed", False) is True


def test_storage_guard_no_client_safe() -> None:
    """Если client=None — guard не падает."""
    mixin = SessionMixin.__new__(SessionMixin)
    mixin.client = None  # type: ignore[attr-defined]
    # не должен бросать
    mixin._arm_storage_shutdown_guard()


def test_storage_guard_no_storage_safe() -> None:
    """Если storage отсутствует — guard не падает."""
    mixin = SessionMixin.__new__(SessionMixin)
    client = MagicMock()
    client.storage = None
    mixin.client = client  # type: ignore[attr-defined]
    mixin._arm_storage_shutdown_guard()


def test_is_sqlite_io_error_recognises_closed_database() -> None:
    """`_is_sqlite_io_error` должен матчить 'closed database' (ProgrammingError)."""
    exc = sqlite3.ProgrammingError("Cannot operate on a closed database.")
    assert SessionMixin._is_sqlite_io_error(exc) is True


def test_sentry_filter_drops_closed_database_in_exception_value() -> None:
    """Sentry filter должен дропать события про closed database."""
    event = {
        "exception": {
            "values": [
                {
                    "type": "ProgrammingError",
                    "value": "Cannot operate on a closed database.",
                }
            ]
        }
    }
    assert _before_send(event, {}) is None


def test_sentry_filter_drops_closed_database_in_message() -> None:
    """Sentry filter дропает 'closed database' через message field."""
    event = {"message": "ProgrammingError: Cannot operate on a closed database."}
    assert _before_send(event, {}) is None


@pytest.mark.parametrize(
    "value",
    [
        "Cannot operate on a closed database.",
        "sqlite3.ProgrammingError: Cannot operate on a closed database",
    ],
)
def test_sentry_filter_handles_variations(value: str) -> None:
    """Любая вариация closed-db текста должна дропаться."""
    event = {"exception": {"values": [{"value": value}]}}
    assert _before_send(event, {}) is None


# ── Session 28+: статический helper для произвольного pyrogram client ──


def _make_fake_client_with_storage() -> MagicMock:
    """Минимальный fake Pyrogram-client с storage._get / update_peers."""
    storage = types.SimpleNamespace()
    storage._get = MagicMock(return_value="real_value")

    async def _real_update_peers(peers):
        return ("real_update", peers)

    storage.update_peers = _real_update_peers
    client = MagicMock()
    client.storage = storage
    return client


def test_static_guard_arms_external_client() -> None:
    """`_arm_storage_shutdown_guard_for_client` работает на внешнем client."""
    from src.userbot.session import SessionMixin

    client = _make_fake_client_with_storage()
    SessionMixin._arm_storage_shutdown_guard_for_client(client)

    storage = client.storage
    assert getattr(storage, "_krab_storage_closed", False) is True
    assert getattr(storage, "_krab_storage_guard_installed", False) is True
    # _get теперь подменён на guarded-версию и возвращает None
    assert storage._get("peer_id") is None


def test_static_guard_double_arm_idempotent_external_client() -> None:
    """Повторный arm на тот же external client не дублирует и сохраняет ссылку."""
    from src.userbot.session import SessionMixin

    client = _make_fake_client_with_storage()
    SessionMixin._arm_storage_shutdown_guard_for_client(client)
    storage = client.storage
    first_get = storage._get

    SessionMixin._arm_storage_shutdown_guard_for_client(client)
    assert storage._get is first_get
    assert getattr(storage, "_krab_storage_closed", False) is True


def test_static_guard_handles_none_client() -> None:
    """Helper не падает при client=None (early return)."""
    from src.userbot.session import SessionMixin

    SessionMixin._arm_storage_shutdown_guard_for_client(None)


def test_static_guard_handles_storage_none() -> None:
    """Helper не падает при отсутствующем storage."""
    from src.userbot.session import SessionMixin

    client = MagicMock()
    client.storage = None
    SessionMixin._arm_storage_shutdown_guard_for_client(client)


def test_sentry_core_filter_drops_closed_db_in_storage_path() -> None:
    """`core/sentry_integration._is_noise_event` ловит storage frame, не только session."""
    from src.core.sentry_integration import _is_noise_event

    event = {
        "exception": {
            "values": [
                {
                    "value": "sqlite3.ProgrammingError: Cannot operate on a closed database.",
                    "stacktrace": {
                        "frames": [
                            {"filename": "/site-packages/pyrogram/storage/sqlite_storage.py"},
                        ]
                    },
                }
            ]
        }
    }
    assert _is_noise_event(event) is True


def test_sentry_core_filter_keeps_closed_db_outside_pyrogram() -> None:
    """closed-db из НЕ-pyrogram стека не дропаем — это потенциальный реальный bug."""
    from src.core.sentry_integration import _is_noise_event

    event = {
        "exception": {
            "values": [
                {
                    "value": "Cannot operate on a closed database",
                    "stacktrace": {
                        "frames": [
                            {"filename": "/site-packages/some_other_lib/db.py"},
                        ]
                    },
                }
            ]
        }
    }
    assert _is_noise_event(event) is False
