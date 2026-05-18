# -*- coding: utf-8 -*-
"""S67 W2: defensive verify `dispatcher.groups` post-`_setup_handlers()`.

Контекст
--------
Cold-boot silent death: handler chain bricked даже после `_setup_handlers()`
вернувшегося без ошибок (S67 W1 race investigation). Defensive measure
здесь — log error если `dispatcher.groups` empty после setup, чтобы owner
видел проблему immediately, а не через час мёртвого бота.

Pattern: pyrogram `add_handler` шедулит `loop.create_task(...)` fire-and-forget;
этот task может завершиться ПОСЛЕ `client.start()` → groups empty в начале.

Структурные логи (structlog) не route'ятся в pytest caplog, поэтому здесь
patch'аем module-level logger напрямую — это устойчиво и явно.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.userbot.session as session_module
from src.userbot.session import SessionMixin


class _StubBot(SessionMixin):
    """Минимальный stub SessionMixin для unit-теста `_recreate_client`."""

    def __init__(self, tmp_path: Path, *, populate_groups: bool = True) -> None:
        self._session_workdir = tmp_path
        self.client: object | None = None
        self._setup_called = False
        self._populate_groups = populate_groups

    def _main_session_integrity_preflight(self) -> None:
        # bypass — мы не тестируем preflight, только post-setup verify
        return None

    def _setup_handlers(self) -> None:
        self._setup_called = True
        if self._populate_groups:
            self.client.dispatcher.groups = {0: [object()]}
        else:
            self.client.dispatcher.groups = {}


def _client_factory(*args, **kwargs):
    client = MagicMock()
    client.dispatcher = SimpleNamespace(groups={})
    return client


def test_recreate_client_logs_groups_populated_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: `_setup_handlers` заполнил groups → info-лог populated."""

    bot = _StubBot(tmp_path, populate_groups=True)
    mock_logger = MagicMock()
    monkeypatch.setattr(session_module, "logger", mock_logger)

    with patch.object(session_module, "Client", side_effect=_client_factory):
        bot._recreate_client()

    info_calls = [c for c in mock_logger.info.call_args_list]
    event_names = [c.args[0] for c in info_calls if c.args]
    assert "setup_handlers_groups_populated" in event_names, (
        f"Ожидали setup_handlers_groups_populated; got info events: {event_names}"
    )
    # Не должны срабатывать error/warning ветки
    error_events = [c.args[0] for c in mock_logger.error.call_args_list if c.args]
    assert "setup_handlers_returned_empty_groups" not in error_events


def test_recreate_client_logs_error_on_empty_groups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Race / silent failure: `_setup_handlers` вернулся, но groups пустые."""

    bot = _StubBot(tmp_path, populate_groups=False)
    mock_logger = MagicMock()
    monkeypatch.setattr(session_module, "logger", mock_logger)

    with patch.object(session_module, "Client", side_effect=_client_factory):
        bot._recreate_client()

    error_calls = mock_logger.error.call_args_list
    event_names = [c.args[0] for c in error_calls if c.args]
    assert "setup_handlers_returned_empty_groups" in event_names, (
        f"Ожидали ERROR setup_handlers_returned_empty_groups; got: {event_names}"
    )


def test_recreate_client_logs_warning_when_groups_attr_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edge: у dispatcher нет атрибута `groups` → warning, не падение."""

    bot = _StubBot(tmp_path)

    def factory(*args, **kwargs):
        client = MagicMock()
        # dispatcher без атрибута `groups`
        client.dispatcher = SimpleNamespace()
        return client

    # _setup_handlers — no-op, чтобы не выставить groups на dispatcher
    bot._setup_handlers = lambda: None  # type: ignore[method-assign]

    mock_logger = MagicMock()
    monkeypatch.setattr(session_module, "logger", mock_logger)

    with patch.object(session_module, "Client", side_effect=factory):
        bot._recreate_client()

    warning_calls = mock_logger.warning.call_args_list
    event_names = [c.args[0] for c in warning_calls if c.args]
    assert "setup_handlers_no_dispatcher_groups_attr" in event_names, (
        f"Ожидали warning no_dispatcher_groups_attr; got: {event_names}"
    )
