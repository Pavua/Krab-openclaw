# -*- coding: utf-8 -*-
"""
Wave 108: тесты `src/core/moderation_audit_log.py` и интеграции с chat_ban_cache.

Покрытие:
1. log_action создаёт строку с правильными полями.
2. query_recent возвращает DESC по ts, с фильтрами chat_id/action.
3. Несконфигурированный path → log_action возвращает False, query пустой.
4. context (dict) сериализуется в JSON и парсится назад.
5. Malformed args (пустой chat_id/action) → False, ничего не пишется.
6. limit clamping (отрицательный → 0 строк, огромный → 1000 cap).
7. Интеграция: chat_ban_cache.mark_banned → строка krab_banned_in_chat,
   .clear → строка krab_unbanned_in_chat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.core.chat_ban_cache import ChatBanCache
from src.core.moderation_audit_log import ModerationAuditLog, moderation_audit_log


@pytest.fixture
def audit(tmp_path: Path) -> ModerationAuditLog:
    log = ModerationAuditLog(storage_path=tmp_path / "audit.db")
    return log


def test_log_action_writes_row(audit: ModerationAuditLog) -> None:
    ok = audit.log_action(
        "-100123",
        "ban_user",
        reason="spam",
        by_user_id=42,
        context={"msg_id": 7},
    )
    assert ok is True
    rows = audit.query_recent(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["chat_id"] == "-100123"
    assert r["action"] == "ban_user"
    assert r["reason"] == "spam"
    assert r["by_user_id"] == "42"
    assert r["context"] == {"msg_id": 7}
    assert isinstance(r["ts"], str) and "T" in r["ts"]


def test_query_filters_by_chat_and_action(audit: ModerationAuditLog) -> None:
    audit.log_action("-100A", "ban_user", reason="r1")
    audit.log_action("-100A", "unban_user", reason="r2")
    audit.log_action("-100B", "ban_user", reason="r3")

    rows = audit.query_recent(chat_id="-100A")
    assert len(rows) == 2
    assert {r["action"] for r in rows} == {"ban_user", "unban_user"}

    rows = audit.query_recent(action="ban_user")
    assert len(rows) == 2
    assert {r["chat_id"] for r in rows} == {"-100A", "-100B"}

    rows = audit.query_recent(chat_id="-100B", action="ban_user")
    assert len(rows) == 1


def test_query_returns_desc_by_ts(audit: ModerationAuditLog) -> None:
    # Инжектим разные ts через монотонный счётчик.
    moments = [
        datetime(2026, 5, 12, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 12, 11, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc),
    ]
    idx = [0]

    def fake_now() -> datetime:
        m = moments[idx[0]]
        idx[0] += 1
        return m

    audit._now_fn = fake_now
    audit.log_action("c", "ban_user", reason="first")
    audit.log_action("c", "ban_user", reason="second")
    audit.log_action("c", "ban_user", reason="third")
    rows = audit.query_recent(chat_id="c")
    assert [r["reason"] for r in rows] == ["third", "second", "first"]


def test_unconfigured_path_is_noop(tmp_path: Path) -> None:
    log = ModerationAuditLog()  # без storage_path
    assert log.log_action("c", "ban_user") is False
    assert log.query_recent() == []


def test_malformed_args_rejected(audit: ModerationAuditLog) -> None:
    assert audit.log_action("", "ban_user") is False
    assert audit.log_action("c", "") is False
    assert audit.log_action(None, None) is False
    assert audit.query_recent() == []


def test_limit_clamping(audit: ModerationAuditLog) -> None:
    for i in range(5):
        audit.log_action("c", "ban_user", reason=f"r{i}")
    assert audit.query_recent(limit=0) == []
    assert audit.query_recent(limit=-10) == []
    assert len(audit.query_recent(limit=3)) == 3
    # Большое значение должно работать без ошибки, не больше реально записанного.
    rows = audit.query_recent(limit=100000)
    assert len(rows) == 5


def test_unserializable_context_falls_back_to_null(audit: ModerationAuditLog) -> None:
    class Weird:
        def __repr__(self) -> str:
            return "Weird()"

    # default=str в json.dumps серилизует через str() — не падает.
    ok = audit.log_action("c", "ban_user", context={"obj": Weird()})
    assert ok is True
    rows = audit.query_recent()
    assert len(rows) == 1
    # context либо валидный dict с str-репрезентацией, либо None — оба ОК.
    ctx = rows[0]["context"]
    assert ctx is None or "obj" in ctx


def test_chat_ban_cache_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """mark_banned / clear → строки в audit log singleton'е."""
    audit_path = tmp_path / "audit_integ.db"
    moderation_audit_log.configure_default_path(audit_path)

    cache = ChatBanCache(storage_path=tmp_path / "ban.json")
    cache.mark_banned("-100X", "UserBannedInChannel", cooldown_hours=6.0)

    rows = moderation_audit_log.query_recent(chat_id="-100X")
    assert any(r["action"] == "krab_banned_in_chat" for r in rows)
    banned_row = [r for r in rows if r["action"] == "krab_banned_in_chat"][0]
    assert banned_row["reason"] == "UserBannedInChannel"
    assert banned_row["context"]["cooldown_hours"] == 6.0

    cache.clear("-100X")
    rows = moderation_audit_log.query_recent(chat_id="-100X")
    actions = [r["action"] for r in rows]
    assert "krab_unbanned_in_chat" in actions
    assert "krab_banned_in_chat" in actions

    # Сброс singleton'а чтобы не утечь в другие тесты.
    moderation_audit_log.configure_default_path(tmp_path / "noop.db")
