# -*- coding: utf-8 -*-
"""
Тесты wire-up Idea 31 multi-persona в `_append_runtime_constraints`.

Проверяем:
- при `KRAB_MULTI_PERSONA_ENABLED=1` persona suffix добавляется в prompt;
- при `KRAB_MULTI_PERSONA_ENABLED=0` (или отсутствии) suffix НЕ добавляется;
- fail-open: исключение из `persona_suffix_for_prompt` не ломает сборку prompt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_config() -> MagicMock:
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = True  # отключаем scheduler-guard для чистоты
    return cfg


def _append(prompt: str, chat_id: str | int | None = "chat-1") -> str:
    """Вызывает `_append_runtime_constraints` под mock-конфигом."""
    from src.userbot.access_control import AccessControlMixin

    with patch("src.config.config", _make_config()):
        return AccessControlMixin._append_runtime_constraints(prompt, chat_id=chat_id)


class TestMultiPersonaWireup:
    """Wire-up Idea 31 в access_control."""

    def test_persona_suffix_added_when_env_enabled(self):
        """KRAB_MULTI_PERSONA_ENABLED=1 → persona suffix добавлен в prompt."""
        marker = "PERSONA_SUFFIX_TEST_MARKER_42"
        with (
            patch.dict("os.environ", {"KRAB_MULTI_PERSONA_ENABLED": "1"}, clear=False),
            patch(
                "src.core.multi_persona.persona_suffix_for_prompt",
                return_value=marker,
            ),
        ):
            out = _append("BASE PROMPT")
        assert marker in out, "persona suffix должен быть добавлен при включённом env-флаге"

    def test_persona_suffix_not_added_when_env_disabled(self):
        """KRAB_MULTI_PERSONA_ENABLED=0 → persona suffix НЕ добавлен (no-op)."""
        marker = "PERSONA_SUFFIX_DISABLED_MARKER"
        # Гарантируем, что env=0 (или удалён) — suffix не должен попасть в prompt.
        with (
            patch.dict("os.environ", {"KRAB_MULTI_PERSONA_ENABLED": "0"}, clear=False),
            patch(
                "src.core.multi_persona.persona_suffix_for_prompt",
                return_value=marker,
            ),
        ):
            out = _append("BASE PROMPT")
        assert marker not in out, "persona suffix не должен добавляться при выключенном флаге"

    def test_persona_failure_is_fail_open(self):
        """Исключение из persona_suffix_for_prompt не должно ломать сборку prompt."""
        with (
            patch.dict("os.environ", {"KRAB_MULTI_PERSONA_ENABLED": "1"}, clear=False),
            patch(
                "src.core.multi_persona.persona_suffix_for_prompt",
                side_effect=RuntimeError("boom"),
            ),
        ):
            out = _append("BASE PROMPT")
        # Базовый prompt и стандартные runtime-правила сохранены
        assert "BASE PROMPT" in out
        assert "паразитных хвостов" in out
