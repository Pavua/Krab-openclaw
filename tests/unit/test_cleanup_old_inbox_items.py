# -*- coding: utf-8 -*-
"""
Тесты для cleanup_old_inbox_items.py.

Проверяем:
1. cutoff считается детерминированно;
2. default cleanup не цепляет свежие owner_request;
3. явный фильтр по message_id сужает выборку до конкретного item-а.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import importlib.util

import pytest

import src.core.inbox_service as inbox_module
from src.core.inbox_service import InboxService


def _load_cleanup_module():
    """Импортирует CLI-скрипт как обычный модуль для unit-тестов."""
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "cleanup_old_inbox_items.py"
    )
    spec = importlib.util.spec_from_file_location("cleanup_old_inbox_items", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_cutoff_iso_uses_days_delta() -> None:
    """cutoff должен отступать ровно на указанное число суток."""
    cleanup_module = _load_cleanup_module()

    cutoff = cleanup_module.build_cutoff_iso(
        older_than_days=3,
        now=datetime(2026, 3, 27, 18, 0, 0, tzinfo=timezone.utc),
    )

    assert cutoff == "2026-03-24T18:00:00+00:00"


def test_select_stale_items_defaults_to_old_owner_requests_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default cleanup должен находить только старые owner_request и не трогать свежие."""
    cleanup_module = _load_cleanup_module()
    service = InboxService(state_path=tmp_path / "inbox.json")

    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-03-19T00:17:49+00:00")
    service.upsert_incoming_owner_request(
        chat_id="312322764",
        message_id="10848",
        text="Старый owner request",
        sender_username="owner",
        chat_type="private",
    )

    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-03-27T17:16:11+00:00")
    service.upsert_incoming_owner_request(
        chat_id="312322764",
        message_id="11428",
        text="Свежий owner request",
        sender_username="owner",
        chat_type="private",
    )
    service.upsert_item(
        dedupe_key="relay:312322764:11402",
        kind="relay_request",
        source="telegram-userbot",
        title="Relay",
        body="Не должен попасть под default cleanup",
        severity="warning",
    )

    stale = cleanup_module.select_stale_items(
        service,
        older_than_days=3,
        kind="owner_request",
        status="open",
    )

    assert len(stale) == 1
    assert stale[0]["metadata"]["message_id"] == "10848"


def test_select_stale_items_honors_message_id_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Фильтр по message_id должен оставлять только явно указанный item."""
    cleanup_module = _load_cleanup_module()
    service = InboxService(state_path=tmp_path / "inbox.json")

    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-03-19T00:17:49+00:00")
    service.upsert_incoming_owner_request(
        chat_id="312322764",
        message_id="10848",
        text="Старый owner request A",
        sender_username="owner",
        chat_type="private",
    )
    monkeypatch.setattr(inbox_module, "_now_utc_iso", lambda: "2026-03-19T01:16:30+00:00")
    service.upsert_incoming_owner_request(
        chat_id="312322764",
        message_id="10897",
        text="Старый owner request B",
        sender_username="owner",
        chat_type="private",
    )

    stale = cleanup_module.select_stale_items(
        service,
        older_than_days=3,
        kind="owner_request",
        status="open",
        message_ids=["10897"],
    )

    assert len(stale) == 1
    assert stale[0]["metadata"]["message_id"] == "10897"
