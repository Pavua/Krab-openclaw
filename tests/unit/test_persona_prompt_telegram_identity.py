# -*- coding: utf-8 -*-
"""
Тесты persona system prompt: Telegram identity routing (session-33, Дашуля incident).

Owner попросил Краба написать @dodik_ggt в ЛС — Краб отправил сообщение
с bot-API аккаунта (display name «Краб»), получатель не понял кто пишет.
Fix: добавили identity_hint в `_append_runtime_constraints`, который
явно инструктирует LLM использовать userbot MCP (yung-nagato) для
личных DM, а bot-API оставлять только для системных алертов.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_config(*, scheduler_enabled: bool = False) -> MagicMock:
    cfg = MagicMock()
    cfg.SCHEDULER_ENABLED = scheduler_enabled
    return cfg


def _append(prompt: str, *, scheduler_enabled: bool = False) -> str:
    from src.userbot.access_control import AccessControlMixin

    with patch("src.config.config", _make_config(scheduler_enabled=scheduler_enabled)):
        return AccessControlMixin._append_runtime_constraints(prompt)


class TestTelegramIdentityHint:
    """Проверки telegram-identity-блока в runtime constraints."""

    def test_prompt_mentions_userbot_mcp_for_personal_dm(self):
        """LLM должен видеть что для личных DM нужен yung-nagato MCP."""
        out = _append("BASE PROMPT")
        assert "mcp__krab-yung-nagato__telegram_send_message" in out
        # Ключевые сценарии личного DM
        assert "в ЛС" in out or "в личку" in out

    def test_prompt_marks_bot_api_as_alerts_only(self):
        """Bot-API должно быть помечено как только для system alerts."""
        out = _append("BASE PROMPT")
        # Упоминание bot-API канала и его ограниченного назначения
        assert "Bot-API" in out
        assert "системных алертов" in out
        # Явный запрет на третьих лиц
        assert "НИКОГДА" in out
        assert "третьими лицами" in out

    def test_prompt_explains_confusion_risk(self):
        """Должна быть объяснена причина — confusion получателя."""
        out = _append("BASE PROMPT")
        assert "confusion" in out or "путают" in out or "кто этот Краб" in out

    def test_existing_constraints_preserved_regression(self):
        """Regression: identity-блок добавляется после существующих и не ломает их."""
        out = _append("BASE PROMPT")
        # base + scheduler + anti-parasite + reply-first + VPN + identity — всё на месте
        assert out.startswith("BASE PROMPT")
        assert "scheduler/cron сейчас выключен" in out
        assert "паразитных хвостов" in out
        assert "Reply-first" in out
        assert "VPN-инструменты" in out
        assert "Telegram identity" in out
        # Identity идёт ПОСЛЕ VPN (последний блок)
        assert out.index("VPN-инструменты") < out.index("Telegram identity")

    def test_identity_hint_idempotent(self):
        """Повторный вызов не дублирует блок."""
        out1 = _append("BASE PROMPT")
        # Прогоняем повторно поверх результата
        from src.userbot.access_control import AccessControlMixin

        with patch("src.config.config", _make_config()):
            out2 = AccessControlMixin._append_runtime_constraints(out1)
        # Блок встречается ровно один раз
        assert out2.count("Telegram identity для отправки сообщений") == 1
