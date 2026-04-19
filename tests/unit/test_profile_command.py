# -*- coding: utf-8 -*-
"""
Тесты команды !profile из src/handlers/command_handlers.py.

Покрытие:
1.  !profile (без аргументов) — показывает текущий профиль
2.  !profile — имя без фамилии
3.  !profile — с username
4.  !profile — без username
5.  !profile — bio присутствует
6.  !profile — bio отсутствует
7.  !profile — фото count > 0
8.  !profile — фото count = 0 (get_chat_photos падает)
9.  !profile — get_me() падает
10. !profile bio <текст> — обновляет bio
11. !profile bio (без текста) — ошибка валидации
12. !profile bio <текст> — update_profile падает
13. !profile name <first> — только имя без фамилии
14. !profile name <first> <last> — имя + фамилия
15. !profile name (без аргументов) — ошибка валидации
16. !profile name <first> <last> — update_profile падает
17. !profile username <uname> — обновляет username
18. !profile username @uname — снимает @ автоматически
19. !profile username (без аргументов) — ошибка валидации
20. !profile username <uname> — update_username падает
21. !profile unknown — неизвестная подкоманда
22. !profile — не-owner получает 403
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_profile

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_user(*, is_owner: bool = True) -> MagicMock:
    """Создаёт мок Telegram-пользователя."""
    u = MagicMock()
    u.id = 123456789
    return u


def _make_access_profile(*, is_owner: bool = True) -> MagicMock:
    ap = MagicMock()
    ap.level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    return ap


def _make_me(
    *,
    first_name: str = "Krab",
    last_name: str = "Bot",
    username: str | None = "krabbot",
    bio: str | None = "AI userbot",
    user_id: int = 999,
) -> MagicMock:
    """Создаёт мок объекта Pyrogram User (get_me())."""
    me = MagicMock()
    me.first_name = first_name
    me.last_name = last_name
    me.username = username
    me.bio = bio
    me.id = user_id
    return me


async def _photos_async_gen(count: int):
    """Асинхронный генератор фото."""
    for i in range(count):
        yield MagicMock()


def _make_bot(
    *,
    is_owner: bool = True,
    me: MagicMock | None = None,
    photo_count: int = 2,
    get_me_exc: Exception | None = None,
    update_profile_exc: Exception | None = None,
    update_username_exc: Exception | None = None,
    photos_exc: Exception | None = None,
) -> MagicMock:
    """Создаёт мок userbot с нужными методами."""
    bot = MagicMock()
    bot._get_access_profile = MagicMock(return_value=_make_access_profile(is_owner=is_owner))
    bot._get_command_args = MagicMock(return_value="")

    if me is None:
        me = _make_me()

    if get_me_exc:
        bot.client.get_me = AsyncMock(side_effect=get_me_exc)
    else:
        bot.client.get_me = AsyncMock(return_value=me)

    if update_profile_exc:
        bot.client.update_profile = AsyncMock(side_effect=update_profile_exc)
    else:
        bot.client.update_profile = AsyncMock(return_value=True)

    if update_username_exc:
        bot.client.update_username = AsyncMock(side_effect=update_username_exc)
    else:
        bot.client.update_username = AsyncMock(return_value=True)

    # get_chat_photos — асинхронный генератор
    if photos_exc:

        async def _photos_fail(*a, **kw):
            raise photos_exc
            yield  # делаем async generator

        bot.client.get_chat_photos = _photos_fail
    else:

        async def _photos_ok(*a, **kw):
            for _ in range(photo_count):
                yield MagicMock()

        bot.client.get_chat_photos = _photos_ok

    return bot


def _make_message(text: str) -> MagicMock:
    """Создаёт мок Telegram-сообщения."""
    msg = MagicMock()
    msg.text = text
    msg.reply = AsyncMock()
    msg.from_user = _make_user()
    return msg


# ---------------------------------------------------------------------------
# Тесты !profile — показ профиля
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_show_basic():
    """!profile показывает профиль с именем, username, bio и фото."""
    me = _make_me(first_name="Krab", last_name="Bot", username="krabbot", bio="AI userbot")
    bot = _make_bot(me=me, photo_count=3)
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    msg.reply.assert_called_once()
    reply = msg.reply.call_args[0][0]
    assert "Krab Bot" in reply
    assert "@krabbot" in reply
    assert "AI userbot" in reply
    assert "3" in reply  # фото count


@pytest.mark.asyncio
async def test_profile_show_no_last_name():
    """!profile — только имя без фамилии."""
    me = _make_me(first_name="Krab", last_name="", username="krabbot", bio=None)
    bot = _make_bot(me=me, photo_count=0)
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    reply = msg.reply.call_args[0][0]
    assert "Krab" in reply
    # без пробела-хвоста в имени
    assert "Krab Bot" not in reply


@pytest.mark.asyncio
async def test_profile_show_no_username():
    """!profile — без username отображает тире."""
    me = _make_me(username=None)
    bot = _make_bot(me=me)
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    reply = msg.reply.call_args[0][0]
    assert "—" in reply


@pytest.mark.asyncio
async def test_profile_show_no_bio():
    """!profile — без bio отображает тире."""
    me = _make_me(bio=None)
    bot = _make_bot(me=me)
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    reply = msg.reply.call_args[0][0]
    assert "—" in reply  # bio = —


@pytest.mark.asyncio
async def test_profile_show_bio_present():
    """!profile — bio отображается если задано."""
    me = _make_me(bio="Telegram AI userbot")
    bot = _make_bot(me=me)
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    reply = msg.reply.call_args[0][0]
    assert "Telegram AI userbot" in reply


@pytest.mark.asyncio
async def test_profile_show_photos_zero_on_exception():
    """!profile — photo count = 0 если get_chat_photos падает."""
    me = _make_me()
    bot = _make_bot(me=me, photos_exc=Exception("no photos"))
    msg = _make_message("!profile")

    await handle_profile(bot, msg)

    reply = msg.reply.call_args[0][0]
    assert "0" in reply


@pytest.mark.asyncio
async def test_profile_show_get_me_fails():
    """!profile — UserInputError если get_me() падает."""
    bot = _make_bot(get_me_exc=RuntimeError("MTProto error"))
    msg = _make_message("!profile")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert (
        "профиль" in exc_info.value.user_message.lower() or "MTProto" in exc_info.value.user_message
    )


# ---------------------------------------------------------------------------
# Тесты !profile bio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_bio_set():
    """!profile bio <текст> — обновляет bio."""
    bot = _make_bot()
    msg = _make_message("!profile bio Mой новый bio-текст")

    await handle_profile(bot, msg)

    bot.client.update_profile.assert_called_once_with(bio="Mой новый bio-текст")
    reply = msg.reply.call_args[0][0]
    assert "Bio обновлено" in reply
    assert "Mой новый bio-текст" in reply


@pytest.mark.asyncio
async def test_profile_bio_empty():
    """!profile bio (без текста) — UserInputError."""
    bot = _make_bot()
    msg = _make_message("!profile bio")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "bio" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_profile_bio_update_fails():
    """!profile bio — UserInputError если update_profile падает."""
    bot = _make_bot(update_profile_exc=ValueError("flood wait"))
    msg = _make_message("!profile bio test bio")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "bio" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# Тесты !profile name
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_name_first_only():
    """!profile name <first> — только имя, фамилия пустая."""
    bot = _make_bot()
    msg = _make_message("!profile name Kraab")

    await handle_profile(bot, msg)

    bot.client.update_profile.assert_called_once_with(first_name="Kraab", last_name="")
    reply = msg.reply.call_args[0][0]
    assert "Kraab" in reply


@pytest.mark.asyncio
async def test_profile_name_first_and_last():
    """!profile name <first> <last> — имя + фамилия."""
    bot = _make_bot()
    msg = _make_message("!profile name Pavel Uvarov")

    await handle_profile(bot, msg)

    bot.client.update_profile.assert_called_once_with(first_name="Pavel", last_name="Uvarov")
    reply = msg.reply.call_args[0][0]
    assert "Pavel Uvarov" in reply


@pytest.mark.asyncio
async def test_profile_name_empty():
    """!profile name (без аргументов) — UserInputError."""
    bot = _make_bot()
    msg = _make_message("!profile name")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert (
        "имя" in exc_info.value.user_message.lower()
        or "name" in exc_info.value.user_message.lower()
    )


@pytest.mark.asyncio
async def test_profile_name_update_fails():
    """!profile name — UserInputError если update_profile падает."""
    bot = _make_bot(update_profile_exc=RuntimeError("flood"))
    msg = _make_message("!profile name NewName")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "имя" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# Тесты !profile username
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_username_set():
    """!profile username <uname> — обновляет username."""
    bot = _make_bot()
    msg = _make_message("!profile username newkrabbot")

    await handle_profile(bot, msg)

    bot.client.update_username.assert_called_once_with("newkrabbot")
    reply = msg.reply.call_args[0][0]
    assert "newkrabbot" in reply


@pytest.mark.asyncio
async def test_profile_username_strips_at():
    """!profile username @uname — снимает @ автоматически."""
    bot = _make_bot()
    msg = _make_message("!profile username @newkrabbot")

    await handle_profile(bot, msg)

    bot.client.update_username.assert_called_once_with("newkrabbot")
    reply = msg.reply.call_args[0][0]
    assert "@newkrabbot" in reply


@pytest.mark.asyncio
async def test_profile_username_empty():
    """!profile username (без аргументов) — UserInputError."""
    bot = _make_bot()
    msg = _make_message("!profile username")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "username" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_profile_username_update_fails():
    """!profile username — UserInputError если update_username падает."""
    bot = _make_bot(update_username_exc=ValueError("invalid username"))
    msg = _make_message("!profile username bad_name")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "username" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# Тесты — неизвестная подкоманда и ACL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_unknown_subcommand():
    """!profile unknown — UserInputError с help-текстом."""
    bot = _make_bot()
    msg = _make_message("!profile foobar")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    # Должен содержать справку
    assert "profile" in exc_info.value.user_message.lower()


@pytest.mark.asyncio
async def test_profile_non_owner_rejected():
    """!profile — не-owner получает ошибку доступа."""
    bot = _make_bot(is_owner=False)
    msg = _make_message("!profile")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "владелец" in exc_info.value.user_message.lower() or "🔒" in exc_info.value.user_message


@pytest.mark.asyncio
async def test_profile_non_owner_bio_rejected():
    """!profile bio — не-owner получает ошибку доступа."""
    bot = _make_bot(is_owner=False)
    msg = _make_message("!profile bio some bio")

    with pytest.raises(UserInputError) as exc_info:
        await handle_profile(bot, msg)

    assert "владелец" in exc_info.value.user_message.lower() or "🔒" in exc_info.value.user_message
