# -*- coding: utf-8 -*-
"""
Wave 44-O-prompt: тесты агентного stance в system prompt для OWNER.

Контекст: Krab agent loop имеет 80+ MCP tools (krab-telegram, krab-telegram-owner,
krab-hammerspoon) через OpenClaw gateway. До Wave 44-O при просьбах вида
"делегируй командам" Krab отвечал ОПИСАНИЕМ команд. Теперь для OWNER в
system prompt добавлен агентный блок: EXECUTE, don't describe, + ссылка
на Krab Swarm group и !swarm-команды. Non-owner stance не меняется.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_config(
    *,
    scheduler_enabled: bool = False,
    non_owner_safe_mode_enabled: bool = True,
    non_owner_safe_prompt: str = "NEUTRAL ASSISTANT PROMPT",
    partial_access_prompt: str = "",
) -> MagicMock:
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = scheduler_enabled
    cfg.NON_OWNER_SAFE_MODE_ENABLED = non_owner_safe_mode_enabled
    cfg.NON_OWNER_SAFE_PROMPT = non_owner_safe_prompt
    cfg.PARTIAL_ACCESS_PROMPT = partial_access_prompt
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


class TestAgenticStanceOwner:
    def test_owner_prompt_contains_execute_directive(self):
        out = _build(is_allowed_sender=True)
        assert "EXECUTE" in out
        assert "АГЕНТНОЕ ПОВЕДЕНИЕ" in out

    def test_owner_prompt_mentions_krab_swarm_group_id(self):
        out = _build(is_allowed_sender=True)
        assert "-1003703978531" in out
        assert "Krab Swarm" in out

    def test_owner_prompt_lists_swarm_commands(self):
        out = _build(is_allowed_sender=True)
        assert "!swarm task create" in out
        assert "!swarm" in out
        assert "loop" in out
        assert "summary" in out
        assert "artifacts" in out

    def test_owner_prompt_lists_team_names(self):
        out = _build(is_allowed_sender=True)
        for team in ("traders", "coders", "analysts", "creative"):
            assert team in out

    def test_owner_prompt_mentions_tool_inventory(self):
        out = _build(is_allowed_sender=True)
        # Telegram + MCP + Hammerspoon hint
        assert "telegram_send_message" in out
        assert "krab_status" in out
        assert "hammerspoon" in out.lower()


class TestAgenticStanceNonOwner:
    def test_non_owner_prompt_does_not_contain_agentic_block(self):
        out = _build(is_allowed_sender=False)
        assert "АГЕНТНОЕ ПОВЕДЕНИЕ" not in out
        assert "EXECUTE" not in out

    def test_non_owner_prompt_does_not_leak_swarm_group_id(self):
        out = _build(is_allowed_sender=False)
        assert "-1003703978531" not in out

    def test_non_owner_prompt_does_not_list_swarm_commands(self):
        out = _build(is_allowed_sender=False)
        assert "!swarm task create" not in out


class TestAgenticPreservesExistingPolicy:
    def test_owner_prompt_still_has_injection_defense(self):
        out = _build(is_allowed_sender=True)
        assert "ЗАЩИТА ОТ ИНЪЕКЦИЙ" in out

    def test_owner_prompt_still_has_runtime_constraints(self):
        out = _build(is_allowed_sender=True)
        # _append_runtime_constraints контент
        assert "паразитных хвостов" in out
        assert "Reply-first" in out
        assert "Telegram identity" in out

    def test_owner_prompt_starts_with_base(self):
        out = _build(is_allowed_sender=True)
        assert out.startswith("BASE OWNER PROMPT")


class TestAgenticStanceV2Strengthened:
    """Wave 44-O-prompt-v2: stronger imperative stance.

    GPT-5.5 ignored the hint-style v1 prompt and continued describing
    !swarm commands instead of executing tool calls. v2 replaces the
    soft hint with imperative language + concrete examples + anti-pattern.
    """

    def test_owner_prompt_uses_imperative_kritichno(self):
        out = _build(is_allowed_sender=True)
        assert "КРИТИЧНО" in out

    def test_owner_prompt_uses_vyzovi_tool_directive(self):
        out = _build(is_allowed_sender=True)
        assert "ВЫЗОВИ tool" in out

    def test_owner_prompt_calls_out_nevypolnenie(self):
        out = _build(is_allowed_sender=True)
        assert "НЕВЫПОЛНЕНИЕ" in out

    def test_owner_prompt_has_concrete_chat_id_example(self):
        out = _build(is_allowed_sender=True)
        # chat_id="-1003703978531" должен фигурировать в конкретном
        # вызове telegram_send_message в примере (не только справочно).
        assert 'chat_id="-1003703978531"' in out

    def test_owner_prompt_includes_anti_pattern_example(self):
        out = _build(is_allowed_sender=True)
        # ❌ маркер anti-pattern (description-only mode = ошибка)
        assert "❌" in out
        assert "ЭТО ОШИБКА" in out

    def test_owner_prompt_has_positive_example_marker(self):
        out = _build(is_allowed_sender=True)
        assert "✅" in out

    def test_owner_prompt_explicit_silent_fallback_warning(self):
        out = _build(is_allowed_sender=True)
        assert "silent fallback" in out
