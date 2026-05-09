# -*- coding: utf-8 -*-
"""
Wave 44-T-orchestrator: tests for consolidated agentic system prompt.

Verifies that the OWNER agentic block contains comprehensive tool inventory
sections (MESSAGING / БРАУЗЕР / APPLE APPS / COMPOSITION ПАТТЕРНЫ),
mentions every tool family, includes memory awareness, money safety jail
bar, injection defense, and that non-owner senders never see this block
(regression of Wave 44-O-prompt-v2 isolation).
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


def _build(*, is_allowed_sender: bool, chat_id=None) -> str:
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
            is_allowed_sender=is_allowed_sender,
            access_level=None,
            chat_id=chat_id,
        )


class TestOwnerSectionHeaders:
    """Each tool-family section must have a clearly visible heading."""

    def test_messaging_section_present(self):
        out = _build(is_allowed_sender=True)
        assert "MESSAGING" in out

    def test_brauzer_section_present(self):
        out = _build(is_allowed_sender=True)
        assert "БРАУЗЕР" in out

    def test_apple_apps_section_present(self):
        out = _build(is_allowed_sender=True)
        assert "APPLE APPS" in out

    def test_composition_patterns_section_present(self):
        out = _build(is_allowed_sender=True)
        assert "COMPOSITION ПАТТЕРНЫ" in out


class TestOwnerToolFamilies:
    """Every advertised tool must be mentioned."""

    def test_telegram_tools_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Telegram" in out
        assert "krab_send_to_swarm.py" in out
        assert "krab_send_dm.py" in out

    def test_discord_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Discord" in out
        assert "krab_send_discord.py" in out

    def test_imessage_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "iMessage" in out
        assert "krab_send_imessage.py" in out

    def test_email_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Email" in out or "email" in out
        assert "krab_send_email.py" in out

    def test_browser_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "krab_browser.py" in out

    def test_notes_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Notes" in out
        assert "krab_notes.py" in out

    def test_calendar_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Calendar" in out
        assert "krab_calendar.py" in out

    def test_reminders_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Reminders" in out
        assert "krab_reminders.py" in out

    def test_music_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Music" in out
        assert "krab_music.py" in out

    def test_spotlight_mentioned(self):
        out = _build(is_allowed_sender=True)
        assert "Spotlight" in out
        assert "krab_spotlight.py" in out


class TestOwnerSafetyAndMemory:
    def test_money_safety_jail_bar_present(self):
        out = _build(is_allowed_sender=True)
        # Russian "ФИНАНСЫ" and reference to banks/transactions
        assert "ФИНАНСЫ" in out
        assert "транзакции" in out or "транзакцию" in out

    def test_injection_defense_maintained(self):
        out = _build(is_allowed_sender=True)
        assert "INJECTION" in out or "ИНЪЕКЦ" in out
        assert "312322764" in out  # OWNER chat_id reference

    def test_memory_awareness_present(self):
        out = _build(is_allowed_sender=True)
        assert "ПАМЯТЬ" in out
        assert "memory recall" in out
        assert "!inbox" in out


class TestOwnerStanceRegression:
    """Wave 44-O-prompt-v2 + Wave 44-R + Wave 44-T must remain in place."""

    def test_execute_directive_remains(self):
        out = _build(is_allowed_sender=True)
        assert "EXECUTE" in out
        assert "АГЕНТНОЕ ПОВЕДЕНИЕ" in out

    def test_swarm_group_id_remains(self):
        out = _build(is_allowed_sender=True)
        assert "-1003703978531" in out
        assert "Krab Swarm" in out

    def test_team_names_listed(self):
        out = _build(is_allowed_sender=True)
        for team in ("traders", "coders", "analysts", "creative"):
            assert team in out

    def test_imperative_kritichno(self):
        out = _build(is_allowed_sender=True)
        assert "КРИТИЧНО" in out
        assert "ВЫЗОВИ tool" in out or "вызывает" in out

    def test_anti_pattern_example_remains(self):
        out = _build(is_allowed_sender=True)
        assert "❌" in out
        assert "ЭТО ОШИБКА" in out


class TestNonOwnerNoLeakage:
    """Non-owner stance must NOT receive any of this content."""

    def test_non_owner_no_agentic_block(self):
        out = _build(is_allowed_sender=False)
        assert "АГЕНТНОЕ ПОВЕДЕНИЕ" not in out
        assert "EXECUTE" not in out

    def test_non_owner_no_swarm_group_id(self):
        out = _build(is_allowed_sender=False)
        assert "-1003703978531" not in out

    def test_non_owner_no_tool_inventory(self):
        out = _build(is_allowed_sender=False)
        assert "TOOL INVENTORY" not in out
        assert "krab_send_to_swarm.py" not in out
        assert "krab_browser.py" not in out
        assert "krab_notes.py" not in out

    def test_non_owner_no_composition_patterns(self):
        out = _build(is_allowed_sender=False)
        assert "COMPOSITION ПАТТЕРНЫ" not in out

    def test_non_owner_no_money_jail_bar(self):
        out = _build(is_allowed_sender=False)
        assert "ФИНАНСЫ — JAIL BAR" not in out


class TestOwnerPromptStartsWithBase:
    def test_starts_with_base_role_prompt(self):
        out = _build(is_allowed_sender=True)
        assert out.startswith("BASE OWNER PROMPT")
