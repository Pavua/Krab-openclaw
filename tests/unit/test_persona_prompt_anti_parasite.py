# -*- coding: utf-8 -*-
"""
Тесты persona system prompt: anti-parasite + reply-first правила (Bug 9 root-cause).

Проверяем, что `_append_runtime_constraints` (общий хвост для всех access-уровней)
содержит:
- запрет паразитных хвостов («если хочешь, могу...» и пр.);
- reply-first правило (приоритет блока «[В ответ на сообщение ...]»);
- сохраняет существующий runtime-guard про выключенный scheduler (regression).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_config(*, scheduler_enabled: bool = False) -> MagicMock:
    """Минимальный mock config для рантайм-ограничений."""
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = scheduler_enabled
    return cfg


def _append(prompt: str, *, scheduler_enabled: bool = False) -> str:
    """Вызывает `_append_runtime_constraints` под mock-конфигом."""
    from src.userbot.access_control import AccessControlMixin

    with patch("src.config.config", _make_config(scheduler_enabled=scheduler_enabled)):
        return AccessControlMixin._append_runtime_constraints(prompt)


class TestPersonaPromptAntiParasite:
    """Проверки правил против паразитных хвостов и reply-first."""

    def test_prompt_contains_anti_parasite_rule(self):
        """Хвост должен запрещать паразитные фразы вида «если хочешь, могу...»."""
        out = _append("BASE PROMPT")
        # Ключевые маркеры anti-parasite-блока.
        assert "паразитных хвостов" in out
        assert "если хочешь, могу" in out
        assert "меню вариантов" in out

    def test_prompt_contains_reply_first_rule(self):
        """Хвост должен описывать reply-first приоритет блока [В ответ на сообщение ...]."""
        out = _append("BASE PROMPT")
        assert "Reply-first" in out
        assert "[В ответ на сообщение" in out
        assert "приоритетнее" in out

    def test_existing_scheduler_guard_preserved_regression(self):
        """Regression: existing scheduler-guard и base prompt не теряются при добавлении новых правил."""
        out = _append("BASE PROMPT", scheduler_enabled=False)
        # base сохранён
        assert out.startswith("BASE PROMPT")
        # scheduler-guard на месте
        assert "scheduler/cron сейчас выключен" in out
        # И новые правила добавлены поверх — порядок: base → scheduler → anti-parasite → reply-first.
        assert out.index("scheduler/cron") < out.index("паразитных хвостов")
        assert out.index("паразитных хвостов") < out.index("Reply-first")
