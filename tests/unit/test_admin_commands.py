# -*- coding: utf-8 -*-
"""
test_admin_commands — Phase 2 Wave 11 (Session 27).

Проверяем:
1. Re-exports из command_handlers доступны (TestReExports).
2. Модуль admin_commands корректно экспортирует все handlers.
3. Базовые сценарии: handle_config, handle_silence, handle_cap,
   handle_chatban, handle_costs, handle_blocklist, handle_role.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.handlers.commands.admin_commands as admin_commands_module
from src.core.access_control import AccessLevel, AccessProfile
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _CONFIG_GROUPS,
    _CONFIG_KEY_DESC,
    _SET_ALIASES,
    _SET_FRIENDLY,
    _TRUST_HELP,
    _costs_aggregate,
    _costs_filter_calls,
    _render_chat_ban_entries,
    _render_config_all,
    _render_config_value,
    handle_acl,
    handle_archive,
    handle_blocklist,
    handle_budget,
    handle_cap,
    handle_chatban,
    handle_cmdblock,
    handle_cmdunblock,
    handle_config,
    handle_costs,
    handle_digest,
    handle_models,
    handle_notify,
    handle_proactivity,
    handle_reasoning,
    handle_role,
    handle_scope,
    handle_set,
    handle_setpanelauth,
    handle_silence,
    handle_trust,
    handle_unarchive,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_user(user_id: int = 1, username: str = "owner") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, username=username)


def _make_message(
    text: str = "!cmd",
    *,
    user_id: int = 1,
    username: str = "owner",
    chat_id: int = 100,
) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        from_user=_make_user(user_id, username),
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(),
        edit=AsyncMock(),
    )


def _make_bot(
    args: str = "",
    *,
    access_level: AccessLevel = AccessLevel.OWNER,
) -> SimpleNamespace:
    return SimpleNamespace(
        _get_command_args=lambda _: args,
        _get_access_profile=lambda user: AccessProfile(
            level=access_level,
            source="test",
            matched_subject="test_subject",
        ),
        _runtime_state={},
        me=SimpleNamespace(id=1),
    )


# ---------------------------------------------------------------------------
# TestReExports — проверяем что все handlers доступны через command_handlers
# ---------------------------------------------------------------------------


class TestReExports:
    """Re-exports из command_handlers.py должны работать после Wave 11."""

    def test_handle_config_is_callable(self) -> None:
        assert callable(handle_config)

    def test_handle_set_is_callable(self) -> None:
        assert callable(handle_set)

    def test_handle_acl_is_callable(self) -> None:
        assert callable(handle_acl)

    def test_handle_scope_is_callable(self) -> None:
        assert callable(handle_scope)

    def test_handle_reasoning_is_callable(self) -> None:
        assert callable(handle_reasoning)

    def test_handle_role_is_callable(self) -> None:
        assert callable(handle_role)

    def test_handle_notify_is_callable(self) -> None:
        assert callable(handle_notify)

    def test_handle_chatban_is_callable(self) -> None:
        assert callable(handle_chatban)

    def test_handle_cmdblock_is_callable(self) -> None:
        assert callable(handle_cmdblock)

    def test_handle_cmdunblock_is_callable(self) -> None:
        assert callable(handle_cmdunblock)

    def test_handle_blocklist_is_callable(self) -> None:
        assert callable(handle_blocklist)

    def test_handle_cap_is_callable(self) -> None:
        assert callable(handle_cap)

    def test_handle_silence_is_callable(self) -> None:
        assert callable(handle_silence)

    def test_handle_costs_is_callable(self) -> None:
        assert callable(handle_costs)

    def test_handle_models_is_callable(self) -> None:
        assert callable(handle_models)

    def test_handle_budget_is_callable(self) -> None:
        assert callable(handle_budget)

    def test_handle_digest_is_callable(self) -> None:
        assert callable(handle_digest)

    def test_handle_archive_is_callable(self) -> None:
        assert callable(handle_archive)

    def test_handle_unarchive_is_callable(self) -> None:
        assert callable(handle_unarchive)

    def test_handle_trust_is_callable(self) -> None:
        assert callable(handle_trust)

    def test_handle_proactivity_is_callable(self) -> None:
        assert callable(handle_proactivity)

    def test_handle_setpanelauth_is_callable(self) -> None:
        assert callable(handle_setpanelauth)

    def test_config_groups_non_empty(self) -> None:
        assert len(_CONFIG_GROUPS) > 0

    def test_config_key_desc_non_empty(self) -> None:
        assert len(_CONFIG_KEY_DESC) > 0

    def test_set_aliases_non_empty(self) -> None:
        assert "autodel" in _SET_ALIASES
        assert "language" in _SET_ALIASES

    def test_set_friendly_non_empty(self) -> None:
        assert "stream_interval" in _SET_FRIENDLY

    def test_trust_help_non_empty(self) -> None:
        assert "trust" in _TRUST_HELP.lower()

    def test_costs_filter_calls_callable(self) -> None:
        assert callable(_costs_filter_calls)

    def test_costs_aggregate_callable(self) -> None:
        assert callable(_costs_aggregate)

    def test_render_chat_ban_entries_callable(self) -> None:
        assert callable(_render_chat_ban_entries)

    def test_render_config_value_callable(self) -> None:
        assert callable(_render_config_value)

    def test_render_config_all_callable(self) -> None:
        assert callable(_render_config_all)


# ---------------------------------------------------------------------------
# TestAdminModuleSource — функции живут в admin_commands, не в command_handlers
# ---------------------------------------------------------------------------


class TestAdminModuleSource:
    """Проверяем что admin_commands модуль содержит все handlers."""

    def test_handle_config_in_admin_module(self) -> None:
        assert hasattr(admin_commands_module, "handle_config")
        assert callable(admin_commands_module.handle_config)

    def test_handle_silence_in_admin_module(self) -> None:
        assert hasattr(admin_commands_module, "handle_silence")
        assert callable(admin_commands_module.handle_silence)

    def test_handle_costs_in_admin_module(self) -> None:
        assert hasattr(admin_commands_module, "handle_costs")
        assert callable(admin_commands_module.handle_costs)

    def test_handle_acl_in_admin_module(self) -> None:
        assert hasattr(admin_commands_module, "handle_acl")
        assert callable(admin_commands_module.handle_acl)

    def test_handle_trust_in_admin_module(self) -> None:
        assert hasattr(admin_commands_module, "handle_trust")
        assert callable(admin_commands_module.handle_trust)


# ---------------------------------------------------------------------------
# TestHandleConfig — базовые сценарии !config
# ---------------------------------------------------------------------------


class TestHandleConfig:
    @pytest.mark.asyncio
    async def test_no_args_shows_all_settings(self) -> None:
        bot = _make_bot("")
        msg = _make_message("!config")

        await handle_config(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "Конфигурация Краба" in text

    @pytest.mark.asyncio
    async def test_unknown_key_raises_user_input_error(self) -> None:
        bot = _make_bot("NONEXISTENT_KEY_XYZ")
        msg = _make_message("!config NONEXISTENT_KEY_XYZ")

        with pytest.raises(UserInputError):
            await handle_config(bot, msg)


# ---------------------------------------------------------------------------
# TestRenderChatBanEntries — форматирование chat ban cache
# ---------------------------------------------------------------------------


class TestRenderChatBanEntries:
    def test_empty_list_returns_empty_text(self) -> None:
        result = _render_chat_ban_entries([])
        assert "пуст" in result.lower()

    def test_single_entry_shown(self) -> None:
        entry = {
            "chat_id": "-100123456",
            "last_error_code": "USER_BANNED_IN_CHANNEL",
            "expires_at": "2099-01-01T00:00:00",
            "hit_count": 3,
        }
        result = _render_chat_ban_entries([entry])
        assert "-100123456" in result
        assert "USER_BANNED_IN_CHANNEL" in result
        assert "3" in result


# ---------------------------------------------------------------------------
# TestCostsAggregate — агрегация вызовов
# ---------------------------------------------------------------------------


class TestCostsAggregate:
    def test_empty_calls_returns_zeros(self) -> None:
        result = _costs_aggregate([])
        assert result["total_cost"] == 0.0
        assert result["calls_count"] == 0

    def test_single_call_aggregated(self) -> None:
        call = SimpleNamespace(
            cost_usd=0.05,
            input_tokens=100,
            output_tokens=200,
            model_id="google/gemini-3-pro-preview",
            timestamp=1_000_000,
        )
        result = _costs_aggregate([call])
        assert result["total_cost"] == pytest.approx(0.05)
        assert result["calls_count"] == 1
        assert result["total_tokens"] == 300
        assert "google" in result["by_provider"]

    def test_costs_filter_all(self) -> None:
        calls = [SimpleNamespace(timestamp=1.0), SimpleNamespace(timestamp=2.0)]
        result = _costs_filter_calls(calls, days=None)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestHandleRole — смена роли
# ---------------------------------------------------------------------------


class TestHandleRole:
    @pytest.mark.asyncio
    async def test_list_shows_roles(self) -> None:
        bot = _make_bot()
        msg = SimpleNamespace(
            text="!role list",
            from_user=_make_user(),
            reply=AsyncMock(),
        )

        await handle_role(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "Роли" in text

    @pytest.mark.asyncio
    async def test_unknown_role_raises(self) -> None:
        bot = _make_bot()
        msg = SimpleNamespace(
            text="!role unknownrole_xyz_noop",
            from_user=_make_user(),
            reply=AsyncMock(),
        )

        with pytest.raises(UserInputError):
            await handle_role(bot, msg)


# ---------------------------------------------------------------------------
# TestHandleChatban — управление chat ban cache
# ---------------------------------------------------------------------------


class TestHandleChatban:
    @pytest.mark.asyncio
    async def test_status_shows_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            admin_commands_module,
            "chat_ban_cache",
            SimpleNamespace(list_entries=lambda: [], clear=lambda x: False),
        )
        bot = _make_bot()
        msg = _make_message("!chatban status")

        await handle_chatban(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "пуст" in text.lower() or "chat ban" in text.lower()

    @pytest.mark.asyncio
    async def test_clear_unknown_chat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            admin_commands_module,
            "chat_ban_cache",
            SimpleNamespace(list_entries=lambda: [], clear=lambda x: False),
        )
        bot = _make_bot()
        msg = _make_message("!chatban clear -100999")

        await handle_chatban(bot, msg)

        msg.reply.assert_awaited_once()
        text = msg.reply.await_args.args[0]
        assert "не был" in text.lower() or "ℹ️" in text
