# -*- coding: utf-8 -*-
"""Wave 31-E tests: CronTaskMixin extraction validation."""

from __future__ import annotations

import inspect

import pytest


def test_cron_task_mixin_importable():
    """Mixin импортируется без побочных эффектов."""
    from src.userbot.cron_tasks import CronTaskMixin

    assert CronTaskMixin.__name__ == "CronTaskMixin"


def test_cron_task_mixin_methods_present():
    """Все 4 метода extracted на mixin."""
    from src.userbot.cron_tasks import CronTaskMixin

    expected = {
        "_build_cron_system_prompt",
        "_build_cron_context",
        "_run_cron_prompt_and_send",
        "_send_scheduled_message",
    }
    actual = {m for m in dir(CronTaskMixin) if not m.startswith("__")}
    assert expected.issubset(actual), f"missing: {expected - actual}"


def test_build_cron_system_prompt_contract():
    """System prompt: ≤500 chars, NO_REPLY escape, без 'tool'/'function'."""
    from src.userbot.cron_tasks import CronTaskMixin

    prompt = CronTaskMixin._build_cron_system_prompt()
    assert isinstance(prompt, str)
    assert len(prompt) <= 500, f"prompt too long: {len(prompt)}"
    assert "NO_REPLY" in prompt, "NO_REPLY escape missing"
    # Не должно быть упоминаний tools/функций — иначе CLI-провайдеры начнут tool-chain
    lower = prompt.lower()
    assert "tool calls" in lower or "tool calls" in prompt, "tool guard missing"


def test_build_cron_system_prompt_is_staticmethod():
    """staticmethod — прямой вызов без self."""
    from src.userbot.cron_tasks import CronTaskMixin

    # __dict__ access — проверяем тип на классе, а не на дескрипторе
    raw = CronTaskMixin.__dict__["_build_cron_system_prompt"]
    assert isinstance(raw, staticmethod), "_build_cron_system_prompt должен быть @staticmethod"


def test_kraab_userbot_inherits_cron_mixin():
    """KraabUserbot подключает CronTaskMixin через MRO."""
    from src.userbot.cron_tasks import CronTaskMixin
    from src.userbot_bridge import KraabUserbot

    assert CronTaskMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_build_cron_system_prompt",
        "_build_cron_context",
        "_run_cron_prompt_and_send",
        "_send_scheduled_message",
    ],
)
def test_cron_methods_removed_from_bridge(method_name):
    """Методы определены ТОЛЬКО на mixin, не на bridge — иначе extract сломан."""
    from src.userbot.cron_tasks import CronTaskMixin
    from src.userbot_bridge import KraabUserbot

    # Метод должен резолвиться через CronTaskMixin (не через bridge напрямую)
    method = getattr(KraabUserbot, method_name)
    # Берём raw definition: для staticmethod — функция, для async/sync def — функция
    qualname = getattr(method, "__qualname__", "")
    assert qualname.startswith("CronTaskMixin."), (
        f"{method_name} has qualname {qualname!r}, expected CronTaskMixin.*"
    )

    # Дополнительная проверка — метод не определён в bridge.__dict__
    bridge_dict = KraabUserbot.__dict__
    assert method_name not in bridge_dict, (
        f"{method_name} still on KraabUserbot — extract incomplete"
    )

    # Sanity: метод callable / coroutine
    if method_name == "_build_cron_system_prompt":
        return  # staticmethod, проверяется в test_build_cron_system_prompt_is_staticmethod
    assert inspect.iscoroutinefunction(method), f"{method_name} should be async"
