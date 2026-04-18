# -*- coding: utf-8 -*-
"""
Тесты fast-path регистрации команды !reset.

Проверяем:
1) "reset" присутствует в USERBOT_KNOWN_COMMANDS → ACL-фильтр пропускает хендлер.
2) "reset" присутствует в OWNER_ONLY_COMMANDS → только владелец может сбросить.
3) handle_reset callable → хендлер существует.
4) can_execute_command("reset") → True для owner, False для non-owner.
5) dry-run не требует LLM: handle_reset отвечает напрямую.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import (
    OWNER_ONLY_COMMANDS,
    USERBOT_KNOWN_COMMANDS,
    AccessLevel,
    AccessProfile,
)

# ---------------------------------------------------------------------------
# 1. Регистрация в USERBOT_KNOWN_COMMANDS
# ---------------------------------------------------------------------------


def test_reset_in_known_commands():
    """!reset должна быть в USERBOT_KNOWN_COMMANDS, иначе ACL-фильтр блокирует хендлер."""
    assert "reset" in USERBOT_KNOWN_COMMANDS, (
        "Команда 'reset' отсутствует в USERBOT_KNOWN_COMMANDS — "
        "ACL-фильтр вернёт False и хендлер никогда не сработает."
    )


# ---------------------------------------------------------------------------
# 2. Только для владельца
# ---------------------------------------------------------------------------


def test_reset_in_owner_only_commands():
    """!reset — деструктивная операция, доступна только владельцу."""
    assert "reset" in OWNER_ONLY_COMMANDS, (
        "Команда 'reset' должна быть в OWNER_ONLY_COMMANDS: она удаляет историю."
    )


# ---------------------------------------------------------------------------
# 3. handle_reset callable
# ---------------------------------------------------------------------------


def test_reset_handler_callable():
    """handle_reset должна быть импортируемой async-функцией."""
    from src.handlers.command_handlers import handle_reset

    assert callable(handle_reset)


# ---------------------------------------------------------------------------
# 4. can_execute_command для owner vs non-owner
# ---------------------------------------------------------------------------


def test_reset_allowed_for_owner():
    owner = AccessProfile(
        level=AccessLevel.OWNER,
        source="self",
        matched_subject="me",
    )
    assert owner.can_execute_command("reset", set(USERBOT_KNOWN_COMMANDS)) is True


def test_reset_denied_for_full_access():
    full = AccessProfile(
        level=AccessLevel.FULL,
        source="acl",
        matched_subject="friend",
    )
    assert full.can_execute_command("reset", set(USERBOT_KNOWN_COMMANDS)) is False


def test_reset_denied_for_partial_access():
    partial = AccessProfile(
        level=AccessLevel.PARTIAL,
        source="acl",
        matched_subject="user",
    )
    assert partial.can_execute_command("reset", set(USERBOT_KNOWN_COMMANDS)) is False


# ---------------------------------------------------------------------------
# 5. dry-run: handle_reset отвечает без вызова LLM
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_dry_run_no_llm():
    """handle_reset --dry-run должна вернуть preview без обращения к LLM."""
    from src.handlers.command_handlers import handle_reset

    # Минимальный мок сообщения с "!reset --dry-run"
    message = MagicMock()
    message.chat = MagicMock()
    message.chat.id = 6435872621
    message.text = "!reset --dry-run"
    # from_user.id != bot.me.id → path через reply (не edit)
    message.from_user = MagicMock()
    message.from_user.id = 999  # другой user → reply, не edit
    message.reply = AsyncMock()
    message.edit = AsyncMock()

    # Мок бота
    bot = MagicMock()
    bot.me = MagicMock()
    bot.me.id = 6435872621
    bot._get_command_args = MagicMock(return_value="--dry-run")

    # Замоканные зависимости reset
    mock_history_cache = MagicMock()
    mock_history_cache.get = MagicMock(return_value=None)
    with (
        patch(
            "src.handlers.command_handlers.openclaw_client",
            MagicMock(_sessions={}),
        ),
        patch(
            "src.handlers.command_handlers.history_cache",
            mock_history_cache,
        ),
        patch(
            "src.core.reset_helpers.clear_archive_db_for_chat",
            AsyncMock(return_value=0),
        ),
        patch(
            "src.core.reset_helpers.count_archive_messages_for_chat",
            MagicMock(return_value=5),
        ),
        patch(
            "src.core.gemini_cache_nonce.invalidate_gemini_cache_for_chat",
            AsyncMock(return_value=None),
        ),
    ):
        await handle_reset(bot, message)

    # handle_reset должна была вызвать reply (dry-run preview), не LLM
    assert message.reply.called, "handle_reset должна была вызвать message.reply с dry-run preview"

    # Проверяем что ответ содержит "dry" или "Dry" — признак dry-run preview
    reply_text = message.reply.call_args[0][0] if message.reply.call_args else ""
    assert any(
        marker in reply_text.lower()
        for marker in ("dry", "preview", "удалит", "будет удал", "ничего не удалено")
    ), f"Ответ dry-run не содержит ожидаемых маркеров: {reply_text!r}"
