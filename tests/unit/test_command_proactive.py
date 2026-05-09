# -*- coding: utf-8 -*-
"""
Tests для !proactive command handler (Wave 39-B-2).

Покрывает:
- !proactive on → все три флага True
- !proactive off → все три флага False
- !proactive status → строка с текущим состоянием
- !proactive joins on → только proactive_joins меняется
- !proactive media off → только proactive_media выключается
- !proactive ai on → только proactive_ai включается
- Неизвестный subcommand → UserInputError
- Non-owner → UserInputError
- Mock policy_store: verify save calls (update_policy)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel, AccessProfile
from src.core.chat_response_policy import ChatMode, ChatResponsePolicy, ChatResponsePolicyStore
from src.core.exceptions import UserInputError
from src.handlers.commands.proactive import handle_proactive

# ── Fixtures и helpers ────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """Свежий store в tmp_path для каждого теста."""
    return ChatResponsePolicyStore(path=tmp_path / "p.json")


def _make_msg(text: str, chat_id: int = 100) -> SimpleNamespace:
    msg = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=1, username="owner"),
        reply=AsyncMock(),
    )
    return msg


def _make_bot(level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    profile = AccessProfile(level=level, source="test")
    return SimpleNamespace(_get_access_profile=lambda u: profile)


# ── ACL ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_owner_rejected(store):
    """Не-owner получает UserInputError."""
    bot = _make_bot(level=AccessLevel.GUEST)
    msg = _make_msg("!proactive on")
    with (
        patch("src.core.chat_response_policy.get_store", return_value=store),
        pytest.raises(UserInputError) as exc,
    ):
        await handle_proactive(bot, msg)
    assert "owner" in (exc.value.user_message or "").lower()


# ── !proactive on ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_on_sets_all_flags(store):
    """!proactive on → все три флага True."""
    bot = _make_bot()
    msg = _make_msg("!proactive on", chat_id=42)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("42")
    assert p.proactive_joins is True
    assert p.proactive_media is True
    assert p.proactive_ai is True


@pytest.mark.asyncio
async def test_proactive_on_reply_contains_confirmation(store):
    """!proactive on → reply содержит подтверждение."""
    bot = _make_bot()
    msg = _make_msg("!proactive on", chat_id=42)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    msg.reply.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    assert "включён" in body or "on" in body.lower()


# ── !proactive off ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_off_sets_all_flags_false(store):
    """!proactive off → все три флага False."""
    # Сначала включаем
    store.update_policy("55", proactive_joins=True, proactive_media=True, proactive_ai=True)
    bot = _make_bot()
    msg = _make_msg("!proactive off", chat_id=55)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("55")
    assert p.proactive_joins is False
    assert p.proactive_media is False
    assert p.proactive_ai is False


@pytest.mark.asyncio
async def test_proactive_off_reply_contains_confirmation(store):
    """!proactive off → reply содержит подтверждение."""
    bot = _make_bot()
    msg = _make_msg("!proactive off", chat_id=55)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "выключен" in body or "off" in body.lower()


# ── !proactive status ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_status_shows_current_state(store):
    """!proactive status → вывод содержит текущее состояние флагов."""
    store.update_policy("7", proactive_joins=True, proactive_media=False, proactive_ai=True)
    bot = _make_bot()
    msg = _make_msg("!proactive status", chat_id=7)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    msg.reply.assert_awaited_once()
    body = msg.reply.await_args.args[0]
    # chat_id в выводе
    assert "7" in body
    # упоминание всех трёх секций
    assert "joins" in body.lower() or "Joins" in body
    assert "media" in body.lower() or "Media" in body
    assert "ai" in body.lower() or "AI" in body


@pytest.mark.asyncio
async def test_proactive_status_no_args_same_as_status_subcommand(store):
    """!proactive (без аргументов) → то же что status."""
    bot = _make_bot()
    msg1 = _make_msg("!proactive", chat_id=8)
    msg2 = _make_msg("!proactive status", chat_id=8)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg1)
        await handle_proactive(bot, msg2)
    # Оба должны вернуть ответ
    assert msg1.reply.await_count == 1
    assert msg2.reply.await_count == 1


@pytest.mark.asyncio
async def test_proactive_status_shows_placeholder_quotas(store):
    """!proactive status → квоты показываются как заглушка."""
    bot = _make_bot()
    msg = _make_msg("!proactive status", chat_id=9)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    body = msg.reply.await_args.args[0]
    # Заглушка квот
    assert "—" in body or "диспетчер" in body.lower()


@pytest.mark.asyncio
async def test_proactive_status_shows_mode(store):
    """!proactive status → показывает текущий режим чата."""
    store.update_policy("13", mode=ChatMode.CAUTIOUS)
    bot = _make_bot()
    msg = _make_msg("!proactive status", chat_id=13)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    body = msg.reply.await_args.args[0]
    assert "cautious" in body.lower()


# ── !proactive joins on/off ───────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_joins_on_only_joins_changes(store):
    """!proactive joins on → только proactive_joins = True; media и ai не трогаются."""
    store.update_policy("20", proactive_joins=False, proactive_media=False, proactive_ai=False)
    bot = _make_bot()
    msg = _make_msg("!proactive joins on", chat_id=20)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("20")
    assert p.proactive_joins is True
    assert p.proactive_media is False  # не тронут
    assert p.proactive_ai is False  # не тронут


@pytest.mark.asyncio
async def test_proactive_joins_off_toggles_joins(store):
    """!proactive joins off → proactive_joins = False."""
    store.update_policy("21", proactive_joins=True, proactive_media=True, proactive_ai=True)
    bot = _make_bot()
    msg = _make_msg("!proactive joins off", chat_id=21)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("21")
    assert p.proactive_joins is False
    assert p.proactive_media is True  # не тронут
    assert p.proactive_ai is True  # не тронут


# ── !proactive media on/off ───────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_media_off_only_media_changes(store):
    """!proactive media off → только proactive_media = False; joins и ai не трогаются."""
    store.update_policy("30", proactive_joins=True, proactive_media=True, proactive_ai=True)
    bot = _make_bot()
    msg = _make_msg("!proactive media off", chat_id=30)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("30")
    assert p.proactive_joins is True  # не тронут
    assert p.proactive_media is False
    assert p.proactive_ai is True  # не тронут


@pytest.mark.asyncio
async def test_proactive_media_on_enables_media(store):
    """!proactive media on → proactive_media = True."""
    store.update_policy("31", proactive_media=False)
    bot = _make_bot()
    msg = _make_msg("!proactive media on", chat_id=31)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    assert store.get_policy("31").proactive_media is True


# ── !proactive ai on/off ──────────────────────────────────────


@pytest.mark.asyncio
async def test_proactive_ai_on_only_ai_changes(store):
    """!proactive ai on → только proactive_ai = True; joins и media не трогаются."""
    store.update_policy("40", proactive_joins=False, proactive_media=False, proactive_ai=False)
    bot = _make_bot()
    msg = _make_msg("!proactive ai on", chat_id=40)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)

    p = store.get_policy("40")
    assert p.proactive_joins is False  # не тронут
    assert p.proactive_media is False  # не тронут
    assert p.proactive_ai is True


@pytest.mark.asyncio
async def test_proactive_ai_off_disables_ai(store):
    """!proactive ai off → proactive_ai = False."""
    store.update_policy("41", proactive_ai=True)
    bot = _make_bot()
    msg = _make_msg("!proactive ai off", chat_id=41)
    with patch("src.core.chat_response_policy.get_store", return_value=store):
        await handle_proactive(bot, msg)
    assert store.get_policy("41").proactive_ai is False


# ── Invalid args ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_subcommand_raises_userinputerror(store):
    """Неизвестный subcommand → UserInputError."""
    bot = _make_bot()
    msg = _make_msg("!proactive frobnicate")
    with (
        patch("src.core.chat_response_policy.get_store", return_value=store),
        pytest.raises(UserInputError),
    ):
        await handle_proactive(bot, msg)


@pytest.mark.asyncio
async def test_joins_without_onoff_raises_userinputerror(store):
    """!proactive joins (без on/off) → UserInputError."""
    bot = _make_bot()
    msg = _make_msg("!proactive joins")
    with (
        patch("src.core.chat_response_policy.get_store", return_value=store),
        pytest.raises(UserInputError),
    ):
        await handle_proactive(bot, msg)


@pytest.mark.asyncio
async def test_media_invalid_value_raises_userinputerror(store):
    """!proactive media maybe → UserInputError."""
    bot = _make_bot()
    msg = _make_msg("!proactive media maybe")
    with (
        patch("src.core.chat_response_policy.get_store", return_value=store),
        pytest.raises(UserInputError),
    ):
        await handle_proactive(bot, msg)


@pytest.mark.asyncio
async def test_ai_invalid_value_raises_userinputerror(store):
    """!proactive ai yes → UserInputError (только on/off допустимы)."""
    bot = _make_bot()
    msg = _make_msg("!proactive ai yes")
    with (
        patch("src.core.chat_response_policy.get_store", return_value=store),
        pytest.raises(UserInputError),
    ):
        await handle_proactive(bot, msg)


# ── Mock store: verify save calls ────────────────────────────


@pytest.mark.asyncio
async def test_proactive_on_calls_update_policy(tmp_path):
    """!proactive on → store.update_policy вызывается с правильными kwargs."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="50")
    mock_store.update_policy.return_value = policy
    mock_store.get_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive on", chat_id=50)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_called_once_with(
        "50",
        proactive_joins=True,
        proactive_media=True,
        proactive_ai=True,
    )


@pytest.mark.asyncio
async def test_proactive_off_calls_update_policy(tmp_path):
    """!proactive off → store.update_policy вызывается с False для всех."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="51")
    mock_store.update_policy.return_value = policy
    mock_store.get_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive off", chat_id=51)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_called_once_with(
        "51",
        proactive_joins=False,
        proactive_media=False,
        proactive_ai=False,
    )


@pytest.mark.asyncio
async def test_proactive_joins_on_calls_update_policy(tmp_path):
    """!proactive joins on → store.update_policy вызывается только с proactive_joins=True."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="60")
    mock_store.update_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive joins on", chat_id=60)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_called_once_with("60", proactive_joins=True)


@pytest.mark.asyncio
async def test_proactive_media_off_calls_update_policy(tmp_path):
    """!proactive media off → store.update_policy вызывается только с proactive_media=False."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="61")
    mock_store.update_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive media off", chat_id=61)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_called_once_with("61", proactive_media=False)


@pytest.mark.asyncio
async def test_proactive_ai_on_calls_update_policy(tmp_path):
    """!proactive ai on → store.update_policy вызывается только с proactive_ai=True."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="62")
    mock_store.update_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive ai on", chat_id=62)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_called_once_with("62", proactive_ai=True)


@pytest.mark.asyncio
async def test_status_does_not_call_update_policy(tmp_path):
    """!proactive status → store.update_policy НЕ вызывается."""
    mock_store = MagicMock()
    policy = ChatResponsePolicy(chat_id="70")
    mock_store.get_policy.return_value = policy

    bot = _make_bot()
    msg = _make_msg("!proactive status", chat_id=70)
    with patch("src.core.chat_response_policy.get_store", return_value=mock_store):
        await handle_proactive(bot, msg)

    mock_store.update_policy.assert_not_called()
