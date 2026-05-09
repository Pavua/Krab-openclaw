# -*- coding: utf-8 -*-
"""
Wave 46-B-tools-awareness: тесты на explicit owner authorization для
Telegram/iMessage в agentic system prompt.

Контекст инцидента (Session 43, 23:44):
Owner попросил Krab писать его отцу @SergeyRG в Telegram и читать iMessage
переписку — Krab refused с фразами "нет подтверждённого Telegram userbot
tool-call" и "не буду читать просто так". Это был prompt gap, а не tools
gap: tools (krab_send_dm.py + MCP telegram_resolve_username + MCP iMessage
read tools) реально доступны.

Wave 46-B расширяет agentic_stance явным разделом "OWNER AUTHORIZATION
UNLOCKS TOOLS", чтобы Krab не отказывал в legitimate owner-authorized
коммуникациях.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = False
    cfg.NON_OWNER_SAFE_MODE_ENABLED = True
    cfg.NON_OWNER_SAFE_PROMPT = "NEUTRAL ASSISTANT PROMPT"
    cfg.PARTIAL_ACCESS_PROMPT = ""
    return cfg


def _build_owner_prompt() -> str:
    """Собирает owner-промпт с замоканными внешними зависимостями."""
    from src.userbot.access_control import AccessControlMixin

    class _Bot(AccessControlMixin):
        current_role = "default"

    with (
        patch("src.config.config", _make_config()),
        patch(
            "src.employee_templates.get_role_prompt",
            return_value="BASE OWNER PROMPT",
        ),
        patch(
            "src.core.openclaw_workspace.load_workspace_prompt_bundle",
            return_value="",
        ),
    ):
        return _Bot()._build_system_prompt_for_sender(
            is_allowed_sender=True,
            access_level=None,
            chat_id=None,
        )


class TestOwnerAuthorizationSection:
    """Главный раздел OWNER AUTHORIZATION должен быть присутствует."""

    def test_agentic_stance_mentions_owner_authorization_unlocks(self) -> None:
        out = _build_owner_prompt()
        assert "OWNER AUTHORIZATION" in out
        # Ключевая фраза-триггер которую распознаёт Krab.
        assert "разрешаю тебе" in out

    def test_agentic_stance_lists_explicit_authorization_phrases(self) -> None:
        out = _build_owner_prompt()
        # Все три canonical phrasings (отец/мама/брат, пиши ему/ей,
        # наша переписка ОК, можешь читать).
        assert "это мой" in out
        assert "пиши" in out
        # хотя бы один из родственных триггеров должен быть в списке
        assert any(rel in out for rel in ("отец", "мама", "брат"))


class TestImessageToolsListed:
    """Все три iMessage MCP-tools (history/search/unread) перечислены."""

    def test_agentic_stance_mentions_imessage_history_search(self) -> None:
        out = _build_owner_prompt()
        assert "imessage_history" in out
        assert "imessage_search" in out
        assert "imessage_unread" in out


class TestTelegramResolveUsername:
    """MCP telegram_resolve_username должен быть упомянут как resolve-шаг."""

    def test_agentic_stance_mentions_telegram_resolve_username(self) -> None:
        out = _build_owner_prompt()
        assert "telegram_resolve_username" in out
        # И send-комплемент (либо MCP, либо script) должен быть рядом.
        assert "krab_send_dm.py" in out


class TestRefusalAntiPatterns:
    """DO NOT REFUSE — explicit warning против старых отказов."""

    def test_agentic_stance_warns_against_refusal_phrases(self) -> None:
        out = _build_owner_prompt()
        assert "DO NOT REFUSE" in out
        # Ровно та фраза которую Krab употребил в инциденте.
        assert "нет подтверждённого" in out
        # И вторая фраза-симптом.
        assert "просто так" in out


class TestToolInventoryUsernameFlow:
    """Tool inventory должен показывать resolve→send pattern, а не только id."""

    def test_tool_inventory_includes_send_dm_with_username_flow(self) -> None:
        out = _build_owner_prompt()
        # Шаг 1 (resolve) и шаг 2 (send) явно упомянуты в одном блоке.
        assert "Шаг 1" in out or "resolve" in out.lower()
        assert "telegram_resolve_username" in out
        assert "krab_send_dm.py" in out
        # Пример с @SergeyRG (или каким-то placeholder username) показывает
        # что не нужно знать chat_id заранее.
        assert "@SergeyRG" in out or "@username" in out


class TestSafetyConstraintsPreserved:
    """Money safety + bash_guard должны остаться нетронутыми."""

    def test_money_safety_still_applies(self) -> None:
        out = _build_owner_prompt()
        # money safety раздел сохранён.
        assert "money safety" in out or "ФИНАНСЫ" in out
        # CONFIRM tier явно упомянут (Wave 44-T-money-safety-v2).
        assert "CONFIRM" in out

    def test_bash_guard_still_applies(self) -> None:
        out = _build_owner_prompt()
        # bash_guard упомянут как all-still-applies guard.
        assert "bash_guard" in out


class TestNonOwnerNoLeakage:
    """Non-owner не должен видеть OWNER AUTHORIZATION блок."""

    def test_non_owner_no_owner_authorization_section(self) -> None:
        from src.userbot.access_control import AccessControlMixin

        class _Bot(AccessControlMixin):
            current_role = "default"

        with (
            patch("src.config.config", _make_config()),
            patch(
                "src.employee_templates.get_role_prompt",
                return_value="BASE OWNER PROMPT",
            ),
            patch(
                "src.core.openclaw_workspace.load_workspace_prompt_bundle",
                return_value="",
            ),
        ):
            out = _Bot()._build_system_prompt_for_sender(
                is_allowed_sender=False,
                access_level=None,
                chat_id=None,
            )
        assert "OWNER AUTHORIZATION" not in out
        assert "разрешаю тебе" not in out
        assert "imessage_history" not in out
