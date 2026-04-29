# -*- coding: utf-8 -*-
"""
Тесты команды !scope — управление ACL правами из Telegram.

Покрываем:
1. !scope без аргументов — показывает уровень доступа отправителя (owner);
2. !scope без аргументов — показывает уровень доступа guest;
3. !scope list — owner видит все ACL-записи;
4. !scope list — не-owner получает ошибку;
5. !scope grant <user_id> full — owner выдаёт full доступ;
6. !scope grant <user_id> partial — owner выдаёт partial доступ;
7. !scope grant — неверный уровень (не full/partial) → ошибка;
8. !scope grant — слишком мало аргументов → ошибка;
9. !scope grant — не-owner → ошибка;
10. !scope revoke <user_id> — удаляет существующие права;
11. !scope revoke <user_id> — subject не найден → informational reply;
12. !scope revoke — не-owner → ошибка;
13. !scope <unknown> — неизвестное действие → ошибка.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as cmd_module
import src.handlers.commands.admin_commands as admin_cmd_module
from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_scope

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_user(user_id: int = 1, username: str = "owner") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=username)


def _make_message(args: str, *, user_id: int = 1, username: str = "owner") -> SimpleNamespace:
    return SimpleNamespace(
        from_user=_make_user(user_id, username),
        reply=AsyncMock(),
    )


def _make_bot(
    args: str,
    *,
    access_level: AccessLevel = AccessLevel.OWNER,
    user_id: int = 1,
    username: str = "owner",
) -> SimpleNamespace:
    """Создаёт мок-бот с заданным уровнем доступа."""
    return SimpleNamespace(
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(
            level=access_level,
            source="test",
            matched_subject=str(user_id),
        ),
    )


# ---------------------------------------------------------------------------
# !scope (без аргументов) — показывает свой уровень доступа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_no_args_shows_owner_level() -> None:
    """!scope без аргументов показывает owner-уровень."""
    bot = _make_bot("", access_level=AccessLevel.OWNER)
    message = _make_message("", username="owner")

    await handle_scope(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "owner" in text
    assert "Уровень доступа" in text


@pytest.mark.asyncio
async def test_scope_no_args_shows_guest_level() -> None:
    """!scope без аргументов корректно показывает guest-уровень."""
    bot = _make_bot("", access_level=AccessLevel.GUEST)
    message = _make_message("", user_id=999, username="stranger")

    await handle_scope(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "guest" in text


@pytest.mark.asyncio
async def test_scope_no_args_shows_partial_level() -> None:
    """!scope без аргументов корректно показывает partial-уровень."""
    bot = _make_bot("", access_level=AccessLevel.PARTIAL)
    message = _make_message("", user_id=42, username="reader")

    await handle_scope(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "partial" in text


@pytest.mark.asyncio
async def test_scope_no_args_shows_username_at() -> None:
    """!scope показывает @username когда он есть."""
    bot = _make_bot("", access_level=AccessLevel.FULL)
    message = _make_message("", user_id=7, username="myuser")

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "@myuser" in text


@pytest.mark.asyncio
async def test_scope_no_args_shows_id_when_no_username() -> None:
    """!scope показывает id: когда username отсутствует."""
    user = SimpleNamespace(id=555, username="")
    message = SimpleNamespace(from_user=user, reply=AsyncMock())
    bot = SimpleNamespace(
        _get_command_args=lambda _: "",
        _get_access_profile=lambda u: AccessProfile(
            level=AccessLevel.GUEST, source="test", matched_subject=""
        ),
    )

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "id:555" in text


# ---------------------------------------------------------------------------
# !scope list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_list_shows_all_acl_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope list отображает owner/full/partial записи."""
    bot = _make_bot("list", access_level=AccessLevel.OWNER)
    message = _make_message("list")
    monkeypatch.setattr(
        admin_cmd_module,
        "load_acl_runtime_state",
        lambda: {"owner": ["boss"], "full": ["alice"], "partial": ["bob"]},
    )

    await handle_scope(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "boss" in text
    assert "alice" in text
    assert "bob" in text
    assert "ACL-записи" in text


@pytest.mark.asyncio
async def test_scope_list_rejected_for_non_owner() -> None:
    """!scope list поднимает UserInputError для non-owner."""
    bot = _make_bot("list", access_level=AccessLevel.FULL)
    message = _make_message("list")

    with pytest.raises(UserInputError) as exc_info:
        await handle_scope(bot, message)
    assert "только владельцу" in str(exc_info.value.user_message or "").lower()


@pytest.mark.asyncio
async def test_scope_list_empty_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope list работает при пустом ACL-файле."""
    bot = _make_bot("list", access_level=AccessLevel.OWNER)
    message = _make_message("list")
    monkeypatch.setattr(
        admin_cmd_module,
        "load_acl_runtime_state",
        lambda: {"owner": [], "full": [], "partial": []},
    )

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    # Пустые уровни показываются как '-'
    assert "-" in text


# ---------------------------------------------------------------------------
# !scope grant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_grant_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope grant 123 full выдаёт full доступ."""
    bot = _make_bot("grant 123456789 full", access_level=AccessLevel.OWNER)
    message = _make_message("grant 123456789 full")
    monkeypatch.setattr(
        admin_cmd_module,
        "update_acl_subject",
        lambda level, subject, add: {
            "changed": True,
            "subject": "123456789",
            "state": {"owner": [], "full": ["123456789"], "partial": []},
        },
    )

    await handle_scope(bot, message)

    message.reply.assert_awaited_once()
    text = message.reply.await_args.args[0]
    assert "Доступ выдан" in text
    assert "123456789" in text
    assert "full" in text


@pytest.mark.asyncio
async def test_scope_grant_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope grant @reader partial выдаёт partial доступ."""
    bot = _make_bot("grant @reader partial", access_level=AccessLevel.OWNER)
    message = _make_message("grant @reader partial")
    monkeypatch.setattr(
        admin_cmd_module,
        "update_acl_subject",
        lambda level, subject, add: {
            "changed": True,
            "subject": "reader",
            "state": {"owner": [], "full": [], "partial": ["reader"]},
        },
    )

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "reader" in text
    assert "partial" in text


@pytest.mark.asyncio
async def test_scope_grant_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope grant сообщает 'без изменений' если subject уже в ACL."""
    bot = _make_bot("grant 111 full", access_level=AccessLevel.OWNER)
    message = _make_message("grant 111 full")
    monkeypatch.setattr(
        admin_cmd_module,
        "update_acl_subject",
        lambda level, subject, add: {
            "changed": False,
            "subject": "111",
            "state": {"owner": [], "full": ["111"], "partial": []},
        },
    )

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "без изменений" in text


@pytest.mark.asyncio
async def test_scope_grant_invalid_level() -> None:
    """!scope grant с неверным уровнем (owner, guest) → ошибка."""
    bot = _make_bot("grant 123 owner", access_level=AccessLevel.OWNER)
    message = _make_message("grant 123 owner")

    with pytest.raises(UserInputError) as exc_info:
        await handle_scope(bot, message)
    assert "full" in str(exc_info.value.user_message or "")


@pytest.mark.asyncio
async def test_scope_grant_too_few_args() -> None:
    """!scope grant без subject/level → ошибка форматирования."""
    bot = _make_bot("grant 123", access_level=AccessLevel.OWNER)
    message = _make_message("grant 123")

    with pytest.raises(UserInputError) as exc_info:
        await handle_scope(bot, message)
    assert "Формат" in str(exc_info.value.user_message or "")


@pytest.mark.asyncio
async def test_scope_grant_rejected_for_full_user() -> None:
    """!scope grant недоступен full-пользователям."""
    bot = _make_bot("grant 999 partial", access_level=AccessLevel.FULL)
    message = _make_message("grant 999 partial")

    with pytest.raises(UserInputError) as exc_info:
        await handle_scope(bot, message)
    assert "только владельцу" in str(exc_info.value.user_message or "").lower()


@pytest.mark.asyncio
async def test_scope_grant_rejected_for_guest() -> None:
    """!scope grant недоступен guest."""
    bot = _make_bot("grant 999 full", access_level=AccessLevel.GUEST)
    message = _make_message("grant 999 full")

    with pytest.raises(UserInputError):
        await handle_scope(bot, message)


# ---------------------------------------------------------------------------
# !scope revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_revoke_removes_from_both_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope revoke удаляет subject из full и partial."""
    call_log: list[tuple[str, str, bool]] = []

    def fake_update(level, subject, *, add):
        call_log.append((level, subject, add))
        return {
            "changed": True,
            "subject": subject,
            "state": {"owner": [], "full": [], "partial": []},
        }

    bot = _make_bot("revoke 123456789", access_level=AccessLevel.OWNER)
    message = _make_message("revoke 123456789")
    monkeypatch.setattr(admin_cmd_module, "update_acl_subject", fake_update)

    await handle_scope(bot, message)

    # Должны быть вызовы для full и partial
    levels_called = {entry[0] for entry in call_log}
    assert "full" in levels_called
    assert "partial" in levels_called
    # Все вызовы с add=False
    assert all(not entry[2] for entry in call_log)

    text = message.reply.await_args.args[0]
    assert "Доступ отозван" in text


@pytest.mark.asyncio
async def test_scope_revoke_not_found_sends_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope revoke для неизвестного subject сообщает 'не найден'."""
    bot = _make_bot("revoke 999", access_level=AccessLevel.OWNER)
    message = _make_message("revoke 999")
    monkeypatch.setattr(
        admin_cmd_module,
        "update_acl_subject",
        lambda level, subject, add: {
            "changed": False,
            "subject": subject,
            "state": {"owner": [], "full": [], "partial": []},
        },
    )

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "не найден" in text.lower()


@pytest.mark.asyncio
async def test_scope_revoke_rejected_for_non_owner() -> None:
    """!scope revoke недоступен non-owner."""
    bot = _make_bot("revoke 123", access_level=AccessLevel.PARTIAL)
    message = _make_message("revoke 123")

    with pytest.raises(UserInputError):
        await handle_scope(bot, message)


@pytest.mark.asyncio
async def test_scope_revoke_partial_only_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """!scope revoke сообщает только об уровнях, где subject реально был."""
    removed = {"partial"}

    def fake_update(level, subject, *, add):
        return {
            "changed": level in removed,
            "subject": subject,
            "state": {"owner": [], "full": [], "partial": []},
        }

    bot = _make_bot("revoke alice", access_level=AccessLevel.OWNER)
    message = _make_message("revoke alice")
    monkeypatch.setattr(admin_cmd_module, "update_acl_subject", fake_update)

    await handle_scope(bot, message)

    text = message.reply.await_args.args[0]
    assert "partial" in text
    assert (
        "full" not in text or "full" not in text.split("уровней")[1] if "уровней" in text else True
    )


# ---------------------------------------------------------------------------
# !scope <unknown>
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_unknown_action_raises() -> None:
    """!scope <unknown> поднимает UserInputError с перечнем подкоманд."""
    bot = _make_bot("delete 123", access_level=AccessLevel.OWNER)
    message = _make_message("delete 123")

    with pytest.raises(UserInputError) as exc_info:
        await handle_scope(bot, message)
    err = str(exc_info.value.user_message or "")
    assert "grant" in err
    assert "revoke" in err
    assert "list" in err


@pytest.mark.asyncio
async def test_scope_unknown_action_not_owner() -> None:
    """!scope <unknown> для non-owner всё равно поднимает ошибку (не ACL, а action)."""
    bot = _make_bot("delete 123", access_level=AccessLevel.GUEST)
    message = _make_message("delete 123")

    with pytest.raises(UserInputError):
        await handle_scope(bot, message)
