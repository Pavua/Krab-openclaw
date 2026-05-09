# -*- coding: utf-8 -*-
"""
Tests для !dreaming command handler (Wave 44-N-cli).

Покрывает:
- ACL: non-owner → UserInputError
- !dreaming / status → dreaming_status() called, formatted reply
- !dreaming diary → dream_diary() called, truncation работает
- !dreaming repair → dreaming_repair() called
- !dreaming dedupe → dream_diary_dedupe() called
- !dreaming backfill → dream_diary_backfill() called
- !dreaming reset → требует "confirm"
- !dreaming reset confirm → dream_diary_reset() called
- Unknown subcommand → UserInputError
- RPC failure → UserInputError с человекочитаемым сообщением
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.commands.dreaming import (
    _format_diary,
    _format_op_result,
    _format_status,
    handle_dreaming,
)

# ── Fixtures ────────────────────────────────────────────────────


def _make_msg(text: str, chat_id: int = 100, user_id: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=user_id, username="owner"),
        reply=AsyncMock(),
    )


def _make_bot(level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    profile = AccessProfile(level=level, source="test")
    return SimpleNamespace(_get_access_profile=lambda u: profile)


@pytest.fixture
def fake_client():
    """Mock openclaw_client с async stubs для всех dreaming.* методов."""
    client = SimpleNamespace(
        dreaming_status=AsyncMock(
            return_value={
                "enabled": True,
                "last_diary_update": "2026-05-09T10:00:00Z",
                "events_count": 42,
                "short_term_recall_size": 7,
                "diary_path": "/dreams/diary.md",
            }
        ),
        dream_diary=AsyncMock(
            return_value={
                "found": True,
                "path": "/dreams/diary.md",
                "content": "# Dreaming Diary\n\nentry 1\nentry 2",
            }
        ),
        dreaming_repair=AsyncMock(return_value={"ok": True, "archived": 3}),
        dream_diary_dedupe=AsyncMock(return_value={"ok": True, "removed": 5}),
        dream_diary_backfill=AsyncMock(return_value={"ok": True, "added": 10}),
        dream_diary_reset=AsyncMock(return_value={"ok": True, "summary": "diary cleared"}),
    )
    with patch("src.openclaw_client.openclaw_client", client):
        yield client


# ── ACL ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_owner_rejected(fake_client):
    bot = _make_bot(level=AccessLevel.GUEST)
    msg = _make_msg("!dreaming status")
    with pytest.raises(UserInputError) as exc:
        await handle_dreaming(bot, msg)
    assert "owner" in (exc.value.user_message or "").lower()
    fake_client.dreaming_status.assert_not_called()


# ── status ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_default(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming")
    await handle_dreaming(bot, msg)
    fake_client.dreaming_status.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "Dreaming" in body
    assert "on" in body.lower() or "✅" in body


@pytest.mark.asyncio
async def test_status_explicit(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming status")
    await handle_dreaming(bot, msg)
    fake_client.dreaming_status.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "42" in body  # events_count


@pytest.mark.asyncio
async def test_status_unwraps_dreaming_subobject():
    """Если result имеет ключ 'dreaming', метод должен извлечь sub-object."""
    client = SimpleNamespace()
    # имитируем _gateway_jsonrpc который возвращает full status, метод
    # `dreaming_status` сам распаковывает 'dreaming' ключ.
    from src.openclaw_client import OpenClawClient

    instance = OpenClawClient.__new__(OpenClawClient)

    async def fake_rpc(method, params=None, *, timeout=10.0):
        return {"dreaming": {"enabled": False}, "other": "ignored"}

    instance._gateway_jsonrpc = fake_rpc  # type: ignore[method-assign]
    out = await instance.dreaming_status()
    assert out == {"enabled": False}


# ── diary ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diary_subcommand(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming diary")
    await handle_dreaming(bot, msg)
    fake_client.dream_diary.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "entry 1" in body


@pytest.mark.asyncio
async def test_diary_truncated_when_long(fake_client):
    long_content = "x" * 5000
    fake_client.dream_diary.return_value = {
        "found": True,
        "path": "/dreams/diary.md",
        "content": long_content,
    }
    bot = _make_bot()
    msg = _make_msg("!dreaming diary")
    await handle_dreaming(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "обрезано" in body
    assert len(body) < 5000  # truncated


@pytest.mark.asyncio
async def test_diary_not_found(fake_client):
    fake_client.dream_diary.return_value = {"found": False, "path": "/d.md", "content": ""}
    bot = _make_bot()
    msg = _make_msg("!dreaming diary")
    await handle_dreaming(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "не найден" in body.lower()


# ── repair / dedupe / backfill ─────────────────────────────────


@pytest.mark.asyncio
async def test_repair(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming repair")
    await handle_dreaming(bot, msg)
    fake_client.dreaming_repair.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "repair" in body.lower()
    assert "✅" in body


@pytest.mark.asyncio
async def test_dedupe(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming dedupe")
    await handle_dreaming(bot, msg)
    fake_client.dream_diary_dedupe.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "dedupe" in body.lower()


@pytest.mark.asyncio
async def test_backfill(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming backfill")
    await handle_dreaming(bot, msg)
    fake_client.dream_diary_backfill.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "backfill" in body.lower()


# ── reset (destructive, requires confirm) ──────────────────────


@pytest.mark.asyncio
async def test_reset_without_confirm_raises(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming reset")
    with pytest.raises(UserInputError) as exc:
        await handle_dreaming(bot, msg)
    assert "confirm" in (exc.value.user_message or "").lower()
    fake_client.dream_diary_reset.assert_not_called()


@pytest.mark.asyncio
async def test_reset_with_confirm_runs(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming reset confirm")
    await handle_dreaming(bot, msg)
    fake_client.dream_diary_reset.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "reset" in body.lower()


@pytest.mark.asyncio
async def test_reset_with_wrong_extra_raises(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming reset yes")
    with pytest.raises(UserInputError):
        await handle_dreaming(bot, msg)
    fake_client.dream_diary_reset.assert_not_called()


# ── unknown / errors ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_subcommand(fake_client):
    bot = _make_bot()
    msg = _make_msg("!dreaming xyzzy")
    with pytest.raises(UserInputError) as exc:
        await handle_dreaming(bot, msg)
    assert "xyzzy" in (exc.value.user_message or "").lower()


@pytest.mark.asyncio
async def test_rpc_failure_wrapped_as_userinputerror(fake_client):
    fake_client.dreaming_status.side_effect = RuntimeError("gateway down")
    bot = _make_bot()
    msg = _make_msg("!dreaming status")
    with pytest.raises(UserInputError) as exc:
        await handle_dreaming(bot, msg)
    assert "gateway down" in (exc.value.user_message or "")


# ── format helpers ─────────────────────────────────────────────


def test_format_status_empty():
    assert "пуст" in _format_status({}).lower() or "не активирован" in _format_status({}).lower()


def test_format_status_disabled():
    out = _format_status({"enabled": False})
    assert "off" in out.lower() or "⭕" in out


def test_format_diary_empty_content():
    out = _format_diary({"found": True, "path": "/d.md", "content": ""})
    assert "пуст" in out.lower()


def test_format_op_result_failure():
    out = _format_op_result("repair", {"ok": False, "message": "boom"})
    assert "❌" in out
    assert "boom" in out


def test_format_op_result_no_details():
    out = _format_op_result("dedupe", {"ok": True})
    assert "dedupe" in out.lower()
    assert "no details" in out.lower()
