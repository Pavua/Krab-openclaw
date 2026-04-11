# -*- coding: utf-8 -*-
"""
Тесты для swarm team listener и team prompts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.swarm_team_listener import (
    _check_cooldown,
    _handle_team_message,
    is_listeners_enabled,
    register_team_message_handler,
    set_listeners_enabled,
)
from src.core.swarm_team_prompts import TEAM_PROMPTS, get_team_system_prompt

# ------------------------------------------------------------------
# swarm_team_prompts
# ------------------------------------------------------------------


class TestTeamPrompts:
    def test_known_teams(self) -> None:
        for team in ("traders", "coders", "analysts", "creative"):
            prompt = get_team_system_prompt(team)
            assert team.capitalize() in prompt or team in prompt

    def test_unknown_team_fallback(self) -> None:
        prompt = get_team_system_prompt("unknown_team")
        assert "unknown_team" in prompt

    def test_all_prompts_contain_base(self) -> None:
        for prompt in TEAM_PROMPTS.values():
            assert "Краб" in prompt
            assert "yung_nagato" in prompt


# ------------------------------------------------------------------
# listeners toggle
# ------------------------------------------------------------------


class TestListenersToggle:
    def test_default_enabled(self) -> None:
        set_listeners_enabled(True)
        assert is_listeners_enabled() is True

    def test_disable(self) -> None:
        set_listeners_enabled(False)
        assert is_listeners_enabled() is False
        set_listeners_enabled(True)  # cleanup


# ------------------------------------------------------------------
# cooldown
# ------------------------------------------------------------------


class TestCooldown:
    def test_first_call_passes(self) -> None:
        assert _check_cooldown("test_team_cd", 99999) is True

    def test_second_call_blocked(self) -> None:
        _check_cooldown("test_team_cd2", 88888)
        assert _check_cooldown("test_team_cd2", 88888) is False

    def test_different_chats_independent(self) -> None:
        _check_cooldown("test_team_cd3", 11111)
        assert _check_cooldown("test_team_cd3", 22222) is True


# ------------------------------------------------------------------
# _handle_team_message
# ------------------------------------------------------------------


def _make_message(text: str, user_id: int = 100, chat_type: str = "ChatType.PRIVATE") -> MagicMock:
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    msg.from_user = SimpleNamespace(id=user_id, username="testuser")
    msg.chat = SimpleNamespace(id=12345, type=chat_type)
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    return msg


def _make_client(user_id: int = 777, username: str = "swarm_coders_bot") -> MagicMock:
    cl = MagicMock()
    cl.me = SimpleNamespace(id=user_id, username=username)
    cl.get_me = AsyncMock(return_value=cl.me)
    cl.send_chat_action = AsyncMock()
    return cl


async def _async_iter(items):
    for item in items:
        yield item


class TestHandleTeamMessage:
    @pytest.mark.asyncio
    async def test_replies_in_private(self) -> None:
        set_listeners_enabled(True)
        msg = _make_message("Привет, расскажи про BTC")
        cl = _make_client()
        openclaw = MagicMock()
        openclaw.send_message_stream = MagicMock(return_value=_async_iter(["Привет!"]))

        with patch("src.core.swarm_team_listener._check_cooldown", return_value=True):
            await _handle_team_message("traders", cl, msg, openclaw)

        msg.reply.assert_called_once()
        assert "Привет!" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_ignores_own_messages(self) -> None:
        msg = _make_message("test", user_id=777)
        cl = _make_client(user_id=777)
        openclaw = MagicMock()

        await _handle_team_message("coders", cl, msg, openclaw)
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_when_disabled(self) -> None:
        set_listeners_enabled(False)
        msg = _make_message("test")
        cl = _make_client()
        openclaw = MagicMock()

        await _handle_team_message("coders", cl, msg, openclaw)
        msg.reply.assert_not_called()
        set_listeners_enabled(True)

    @pytest.mark.asyncio
    async def test_ignores_group_without_mention(self) -> None:
        msg = _make_message("random group message", chat_type="ChatType.SUPERGROUP")
        cl = _make_client()
        openclaw = MagicMock()

        await _handle_team_message("analysts", cl, msg, openclaw)
        msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_responds_to_mention_in_group(self) -> None:
        set_listeners_enabled(True)
        msg = _make_message("@swarm_coders_bot что думаешь?", chat_type="ChatType.SUPERGROUP")
        cl = _make_client(username="swarm_coders_bot")
        openclaw = MagicMock()
        openclaw.send_message_stream = MagicMock(return_value=_async_iter(["Думаю!"]))

        with patch("src.core.swarm_team_listener._check_cooldown", return_value=True):
            await _handle_team_message("coders", cl, msg, openclaw)

        msg.reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_empty_text(self) -> None:
        msg = _make_message("")
        cl = _make_client()
        openclaw = MagicMock()

        with patch("src.core.swarm_team_listener._check_cooldown", return_value=True):
            await _handle_team_message("creative", cl, msg, openclaw)

        msg.reply.assert_not_called()


# ------------------------------------------------------------------
# register_team_message_handler
# ------------------------------------------------------------------


class TestRegisterHandler:
    def test_registers_handler(self) -> None:
        cl = MagicMock()
        cl.on_message = MagicMock(return_value=lambda f: f)
        openclaw = MagicMock()

        register_team_message_handler("coders", cl, openclaw)
        cl.on_message.assert_called_once()


# ------------------------------------------------------------------
# Расширенные тесты swarm_team_prompts
# ------------------------------------------------------------------


class TestTeamPromptsExtended:
    def test_traders_prompt_contains_team_name(self) -> None:
        prompt = get_team_system_prompt("traders")
        assert "Traders" in prompt

    def test_coders_prompt_contains_team_name(self) -> None:
        prompt = get_team_system_prompt("coders")
        assert "Coders" in prompt

    def test_analysts_prompt_contains_team_name(self) -> None:
        prompt = get_team_system_prompt("analysts")
        assert "Analysts" in prompt

    def test_creative_prompt_contains_team_name(self) -> None:
        prompt = get_team_system_prompt("creative")
        assert "Creative" in prompt

    def test_all_prompts_contain_krab_reference(self) -> None:
        """Все prompts упоминают проект Краб и основного бота."""
        for team, prompt in TEAM_PROMPTS.items():
            assert "Краб" in prompt, f"team={team}: нет упоминания Краб"
            assert "yung_nagato" in prompt, f"team={team}: нет упоминания yung_nagato"

    def test_all_prompts_long_enough(self) -> None:
        """Каждый prompt содержательный — не менее 100 символов."""
        for team, prompt in TEAM_PROMPTS.items():
            assert len(prompt) > 100, f"team={team}: prompt слишком короткий ({len(prompt)} chars)"

    def test_traders_prompt_contains_specialization(self) -> None:
        prompt = get_team_system_prompt("traders")
        assert "трейдинг" in prompt or "крипто" in prompt

    def test_coders_prompt_contains_specialization(self) -> None:
        prompt = get_team_system_prompt("coders")
        assert "Python" in prompt

    def test_analysts_prompt_contains_specialization(self) -> None:
        prompt = get_team_system_prompt("analysts")
        assert "аналитик" in prompt or "OSINT" in prompt

    def test_creative_prompt_contains_specialization(self) -> None:
        prompt = get_team_system_prompt("creative")
        assert "контент" in prompt or "копирайтинг" in prompt

    def test_unknown_team_returns_base_prompt(self) -> None:
        """Неизвестная команда: возвращается базовый prompt с её именем."""
        prompt = get_team_system_prompt("superteam")
        assert "superteam" in prompt
        # базовый prompt содержит ключевые слова
        assert "Краб" in prompt
        assert "yung_nagato" in prompt

    def test_case_insensitive_lookup(self) -> None:
        """get_team_system_prompt нечувствителен к регистру."""
        lower = get_team_system_prompt("traders")
        upper = get_team_system_prompt("TRADERS")
        mixed = get_team_system_prompt("Traders")
        assert lower == upper == mixed

    def test_all_four_teams_present(self) -> None:
        """TEAM_PROMPTS содержит ровно четыре ключевых команды."""
        expected = {"traders", "coders", "analysts", "creative"}
        assert expected.issubset(set(TEAM_PROMPTS.keys()))
