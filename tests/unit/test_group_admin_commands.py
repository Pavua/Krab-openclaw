# -*- coding: utf-8 -*-
"""
Unit-тесты для group_admin_commands (Phase 2 Wave 14, Session 27).
Покрывает: welcome helpers, slowmode constants, afk state, mark/blocked/profile/members/invite.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.commands.group_admin_commands import (
    _MUTE_FOREVER_UNTIL,
    _SLOWMODE_LABELS,
    _SLOWMODE_VALID,
    _WELCOME_TEMPLATE_VARS,
    _load_welcome_config,
    _render_welcome_text,
    _save_welcome_config,
    handle_afk,
    handle_blocked,
    handle_chatmute,
    handle_contacts,
    handle_invite,
    handle_mark,
    handle_members,
    handle_profile,
    handle_slowmode,
    handle_welcome,
)

# ---------------------------------------------------------------------------
# Welcome helpers
# ---------------------------------------------------------------------------


class TestWelcomeHelpers:
    def test_render_welcome_text_all_vars(self):
        tmpl = "Привет, {name} ({username})! Добро пожаловать в {chat}! Вас {count}."
        result = _render_welcome_text(tmpl, name="Иван", username="@ivan", chat="Тест", count=3)
        assert result == "Привет, Иван (@ivan)! Добро пожаловать в Тест! Вас 3."

    def test_render_welcome_text_no_vars(self):
        tmpl = "Просто текст без переменных."
        result = _render_welcome_text(tmpl, name="X", username="@x", chat="C", count=1)
        assert result == "просто текст без переменных.".replace("п", "П", 1)
        assert "Просто текст без переменных." == result

    def test_welcome_template_vars_string(self):
        # Убедиться что все 4 переменные задокументированы
        assert "{name}" in _WELCOME_TEMPLATE_VARS
        assert "{username}" in _WELCOME_TEMPLATE_VARS
        assert "{chat}" in _WELCOME_TEMPLATE_VARS
        assert "{count}" in _WELCOME_TEMPLATE_VARS

    def test_load_welcome_config_missing_file(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "nonexistent.json"
        try:
            cfg = _load_welcome_config()
            assert cfg == {}
        finally:
            mod._WELCOME_FILE = orig

    def test_save_and_load_welcome_config(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        test_file = tmp_path / "welcome.json"
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = test_file
        try:
            data = {"-1001234": {"enabled": True, "template": "Привет, {name}!"}}
            _save_welcome_config(data)
            loaded = _load_welcome_config()
            assert loaded == data
        finally:
            mod._WELCOME_FILE = orig

    def test_load_welcome_config_corrupted_json(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        test_file = tmp_path / "bad.json"
        test_file.write_text("NOT JSON", encoding="utf-8")
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = test_file
        try:
            cfg = _load_welcome_config()
            assert cfg == {}
        finally:
            mod._WELCOME_FILE = orig


# ---------------------------------------------------------------------------
# Slowmode constants
# ---------------------------------------------------------------------------


class TestSlowmodeConstants:
    def test_valid_values(self):
        assert 0 in _SLOWMODE_VALID
        assert 10 in _SLOWMODE_VALID
        assert 3600 in _SLOWMODE_VALID
        assert 999 not in _SLOWMODE_VALID

    def test_labels_cover_valid(self):
        for v in _SLOWMODE_VALID:
            assert v in _SLOWMODE_LABELS, f"Missing label for {v}"

    def test_mute_forever_int32_max(self):
        assert _MUTE_FOREVER_UNTIL == 2_147_483_647


# ---------------------------------------------------------------------------
# handle_welcome
# ---------------------------------------------------------------------------


def _make_bot():
    bot = MagicMock()
    bot._get_access_profile = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    return bot


def _make_message(text="!welcome status", chat_id=100):
    msg = MagicMock()
    msg.text = text
    msg.chat.id = chat_id
    msg.chat.title = "TestChat"
    msg.from_user.first_name = "Тест"
    msg.from_user.username = "testuser"
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    return msg


class TestHandleWelcome:
    @pytest.mark.asyncio
    async def test_status_not_configured(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "w.json"
        try:
            bot = _make_bot()
            msg = _make_message("!welcome status")
            await handle_welcome(bot, msg)
            msg.reply.assert_called_once()
            assert "не настроено" in msg.reply.call_args[0][0]
        finally:
            mod._WELCOME_FILE = orig

    @pytest.mark.asyncio
    async def test_set_template(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "w.json"
        try:
            bot = _make_bot()
            msg = _make_message("!welcome set Привет, {name}!")
            await handle_welcome(bot, msg)
            msg.reply.assert_called_once()
            assert "установлено" in msg.reply.call_args[0][0]
            # Проверяем что сохранилось
            cfg = _load_welcome_config()
            assert str(msg.chat.id) in cfg
        finally:
            mod._WELCOME_FILE = orig

    @pytest.mark.asyncio
    async def test_set_empty_raises(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "w.json"
        try:
            bot = _make_bot()
            msg = _make_message("!welcome set")
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)
        finally:
            mod._WELCOME_FILE = orig

    @pytest.mark.asyncio
    async def test_off_disables(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "w.json"
        # Предустанавливаем конфиг
        _save_welcome_config({"100": {"enabled": True, "template": "Hi"}})
        try:
            bot = _make_bot()
            msg = _make_message("!welcome off")
            await handle_welcome(bot, msg)
            cfg = _load_welcome_config()
            assert not cfg["100"]["enabled"]
        finally:
            mod._WELCOME_FILE = orig

    @pytest.mark.asyncio
    async def test_unknown_subcommand_raises(self, tmp_path):
        import src.handlers.commands.group_admin_commands as mod
        orig = mod._WELCOME_FILE
        mod._WELCOME_FILE = tmp_path / "w.json"
        try:
            bot = _make_bot()
            msg = _make_message("!welcome unknown_subcmd")
            with pytest.raises(UserInputError):
                await handle_welcome(bot, msg)
        finally:
            mod._WELCOME_FILE = orig


# ---------------------------------------------------------------------------
# handle_afk
# ---------------------------------------------------------------------------


class TestHandleAfk:
    def _make_afk_bot(self, afk_mode=False, afk_since=0.0, afk_reason=""):
        bot = _make_bot()
        bot._afk_mode = afk_mode
        bot._afk_since = afk_since
        bot._afk_reason = afk_reason
        bot._afk_replied_chats = set()
        return bot

    @pytest.mark.asyncio
    async def test_afk_activate(self):
        bot = self._make_afk_bot()
        msg = _make_message("!afk причина")
        await handle_afk(bot, msg)
        assert bot._afk_mode is True
        assert bot._afk_reason == "причина"
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_afk_off_when_not_active(self):
        bot = self._make_afk_bot(afk_mode=False)
        msg = _make_message("!afk off")
        await handle_afk(bot, msg)
        msg.reply.assert_called_once()
        assert "не активен" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_afk_off_when_active(self):
        import time
        bot = self._make_afk_bot(afk_mode=True, afk_since=time.time() - 120)
        msg = _make_message("!afk off")
        await handle_afk(bot, msg)
        assert bot._afk_mode is False
        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_back_deactivates(self):
        import time
        bot = self._make_afk_bot(afk_mode=True, afk_since=time.time() - 60)
        msg = _make_message("!back")
        await handle_afk(bot, msg)
        assert bot._afk_mode is False
        assert "обратно" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_afk_status_inactive(self):
        bot = self._make_afk_bot()
        msg = _make_message("!afk status")
        await handle_afk(bot, msg)
        assert "не активен" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_afk_status_active(self):
        import time
        bot = self._make_afk_bot(afk_mode=True, afk_since=time.time() - 90, afk_reason="тест")
        msg = _make_message("!afk status")
        await handle_afk(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "AFK активен" in reply_text
        assert "тест" in reply_text


# ---------------------------------------------------------------------------
# handle_mark
# ---------------------------------------------------------------------------


class TestHandleMark:
    def _make_owner_bot(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.OWNER
        bot._get_access_profile.return_value = profile
        bot.me = MagicMock()
        bot.me.id = 99999
        bot.client = MagicMock()
        bot.client.read_chat_history = AsyncMock()
        bot.client.mark_chat_unread = AsyncMock()
        bot.client.get_dialogs = MagicMock()
        return bot

    @pytest.mark.asyncio
    async def test_mark_non_owner_raises(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.GUEST
        bot._get_access_profile.return_value = profile
        bot._get_command_args = MagicMock(return_value="read")
        msg = _make_message("!mark read")
        with pytest.raises(UserInputError):
            await handle_mark(bot, msg)

    @pytest.mark.asyncio
    async def test_mark_read(self):
        bot = self._make_owner_bot()
        bot._get_command_args = MagicMock(return_value="read")
        msg = _make_message("!mark read")
        msg.from_user.id = 1  # != bot.me.id
        await handle_mark(bot, msg)
        bot.client.read_chat_history.assert_called_once_with(chat_id=msg.chat.id)

    @pytest.mark.asyncio
    async def test_mark_unknown_raises(self):
        bot = self._make_owner_bot()
        bot._get_command_args = MagicMock(return_value="unknown")
        msg = _make_message("!mark unknown")
        msg.from_user.id = 1
        with pytest.raises(UserInputError):
            await handle_mark(bot, msg)


# ---------------------------------------------------------------------------
# handle_slowmode
# ---------------------------------------------------------------------------


class TestHandleSlowmode:
    def _make_bot_with_client(self):
        bot = _make_bot()
        bot.client = MagicMock()
        bot.client.get_chat = AsyncMock()
        bot.client.set_slow_mode = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_non_group_raises(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!slowmode 60")
        msg.chat.type.name = "PRIVATE"
        with pytest.raises(UserInputError):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_set_valid_value(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!slowmode 60")
        msg.chat.type.name = "SUPERGROUP"
        await handle_slowmode(bot, msg)
        bot.client.set_slow_mode.assert_called_once_with(msg.chat.id, 60)

    @pytest.mark.asyncio
    async def test_invalid_value_raises(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!slowmode 99")
        msg.chat.type.name = "SUPERGROUP"
        with pytest.raises(UserInputError):
            await handle_slowmode(bot, msg)

    @pytest.mark.asyncio
    async def test_off_sets_zero(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!slowmode off")
        msg.chat.type.name = "GROUP"
        await handle_slowmode(bot, msg)
        bot.client.set_slow_mode.assert_called_once_with(msg.chat.id, 0)

    @pytest.mark.asyncio
    async def test_non_digit_raises(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!slowmode abc")
        msg.chat.type.name = "SUPERGROUP"
        with pytest.raises(UserInputError):
            await handle_slowmode(bot, msg)


# ---------------------------------------------------------------------------
# handle_blocked
# ---------------------------------------------------------------------------


class TestHandleBlocked:
    def _make_blocked_bot(self):
        bot = _make_bot()
        bot.client = MagicMock()
        bot.client.get_blocked = AsyncMock(return_value=[])
        bot.client.block_user = AsyncMock()
        bot.client.unblock_user = AsyncMock()
        bot._get_command_args = MagicMock(return_value="")
        return bot

    @pytest.mark.asyncio
    async def test_list_empty(self):
        async def empty_gen():
            return
            yield  # noqa
        bot = self._make_blocked_bot()
        bot.client.get_blocked = empty_gen
        msg = _make_message("!blocked list")
        msg.reply_to_message = None
        bot._get_command_args = MagicMock(return_value="list")
        await handle_blocked(bot, msg)
        assert "пуст" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_add_no_target_raises(self):
        bot = self._make_blocked_bot()
        bot._get_command_args = MagicMock(return_value="add")
        msg = _make_message("!blocked add")
        msg.reply_to_message = None
        with pytest.raises(UserInputError):
            await handle_blocked(bot, msg)

    @pytest.mark.asyncio
    async def test_add_with_username(self):
        bot = self._make_blocked_bot()
        bot._get_command_args = MagicMock(return_value="add @testuser")
        msg = _make_message("!blocked add @testuser")
        msg.reply_to_message = None
        await handle_blocked(bot, msg)
        bot.client.block_user.assert_called_once_with("testuser")

    @pytest.mark.asyncio
    async def test_remove_no_arg_raises(self):
        bot = self._make_blocked_bot()
        bot._get_command_args = MagicMock(return_value="remove")
        msg = _make_message("!blocked remove")
        msg.reply_to_message = None
        with pytest.raises(UserInputError):
            await handle_blocked(bot, msg)


# ---------------------------------------------------------------------------
# handle_invite
# ---------------------------------------------------------------------------


class TestHandleInvite:
    def _make_owner_bot(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.OWNER
        bot._get_access_profile.return_value = profile
        bot.client = MagicMock()
        bot.client.create_chat_invite_link = AsyncMock()
        bot.client.add_chat_members = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_non_owner_raises(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.GUEST
        bot._get_access_profile.return_value = profile
        msg = _make_message("!invite @user")
        msg.command = ["!invite", "@user"]
        with pytest.raises(UserInputError):
            await handle_invite(bot, msg)

    @pytest.mark.asyncio
    async def test_no_args_raises(self):
        bot = self._make_owner_bot()
        msg = _make_message("!invite")
        msg.command = []
        with pytest.raises(UserInputError):
            await handle_invite(bot, msg)

    @pytest.mark.asyncio
    async def test_add_user(self):
        bot = self._make_owner_bot()
        msg = _make_message("!invite @testuser")
        msg.command = ["!invite", "@testuser"]
        await handle_invite(bot, msg)
        bot.client.add_chat_members.assert_called_once_with(msg.chat.id, "@testuser")


# ---------------------------------------------------------------------------
# handle_profile
# ---------------------------------------------------------------------------


class TestHandleProfile:
    def _make_owner_bot(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.OWNER
        bot._get_access_profile.return_value = profile
        bot.client = MagicMock()
        me = MagicMock()
        me.first_name = "Краб"
        me.last_name = None
        me.username = "krab_bot"
        me.id = 12345
        me.bio = "Тестовый bio"
        bot.client.get_me = AsyncMock(return_value=me)
        bot.client.get_chat_photos = MagicMock()
        bot.client.update_profile = AsyncMock()
        bot.client.update_username = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_non_owner_raises(self):
        from src.core.access_control import AccessLevel
        bot = _make_bot()
        profile = MagicMock()
        profile.level = AccessLevel.GUEST
        bot._get_access_profile.return_value = profile
        msg = _make_message("!profile")
        with pytest.raises(UserInputError):
            await handle_profile(bot, msg)

    @pytest.mark.asyncio
    async def test_show_profile(self):
        bot = self._make_owner_bot()

        async def empty_photos(*a, **kw):
            return
            yield

        bot.client.get_chat_photos = empty_photos
        msg = _make_message("!profile")
        await handle_profile(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "Профиль" in reply_text

    @pytest.mark.asyncio
    async def test_bio_update(self):
        bot = self._make_owner_bot()
        msg = _make_message("!profile bio Новый bio текст")
        await handle_profile(bot, msg)
        bot.client.update_profile.assert_called_once()

    @pytest.mark.asyncio
    async def test_bio_empty_raises(self):
        bot = self._make_owner_bot()
        msg = _make_message("!profile bio")
        with pytest.raises(UserInputError):
            await handle_profile(bot, msg)


# ---------------------------------------------------------------------------
# handle_members
# ---------------------------------------------------------------------------


class TestHandleMembers:
    def _make_bot_with_client(self):
        bot = _make_bot()
        bot.client = MagicMock()
        bot.client.get_chat_members_count = AsyncMock(return_value=42)
        bot.client.get_chat_members = MagicMock()
        bot.client.ban_chat_member = AsyncMock()
        bot.client.unban_chat_member = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_private_chat_raises(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!members")
        msg.chat.type.name = "PRIVATE"
        with pytest.raises(UserInputError):
            await handle_members(bot, msg)

    @pytest.mark.asyncio
    async def test_count(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!members")
        msg.chat.type.name = "SUPERGROUP"
        await handle_members(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "42" in reply_text

    @pytest.mark.asyncio
    async def test_kick_no_reply_raises(self):
        bot = self._make_bot_with_client()
        msg = _make_message("!members kick")
        msg.chat.type.name = "SUPERGROUP"
        msg.reply_to_message = None
        with pytest.raises(UserInputError):
            await handle_members(bot, msg)


# ---------------------------------------------------------------------------
# Re-export: handle_* доступны из command_handlers
# ---------------------------------------------------------------------------


def test_re_exports_from_command_handlers():
    """Все handler'ы должны быть доступны через command_handlers namespace."""
    import src.handlers.command_handlers as ch

    for name in [
        "handle_afk",
        "handle_welcome",
        "handle_new_chat_members",
        "handle_mark",
        "handle_slowmode",
        "handle_chatmute",
        "handle_contacts",
        "handle_invite",
        "handle_blocked",
        "handle_profile",
        "handle_members",
        "_WELCOME_FILE",
        "_WELCOME_TEMPLATE_VARS",
        "_load_welcome_config",
        "_save_welcome_config",
        "_render_welcome_text",
        "_SLOWMODE_VALID",
        "_SLOWMODE_LABELS",
        "_MUTE_FOREVER_UNTIL",
    ]:
        assert hasattr(ch, name), f"command_handlers missing re-export: {name}"
