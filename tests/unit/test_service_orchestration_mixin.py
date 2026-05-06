# -*- coding: utf-8 -*-
"""Wave 31-L tests: ServiceOrchestrationMixin extraction."""

from __future__ import annotations

import inspect

import pytest


def test_service_orchestration_mixin_importable():
    from src.userbot.service_orchestration import ServiceOrchestrationMixin

    assert ServiceOrchestrationMixin.__name__ == "ServiceOrchestrationMixin"


def test_kraab_userbot_inherits_service_orchestration_mixin():
    from src.userbot.service_orchestration import ServiceOrchestrationMixin
    from src.userbot_bridge import KraabUserbot

    assert ServiceOrchestrationMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_ensure_maintenance_started",
        "_ensure_silence_schedule_started",
        "_ensure_memory_indexer_started",
        "_sync_scheduler_runtime",
    ],
)
def test_methods_resolve_via_mixin(method_name):
    from src.userbot.service_orchestration import ServiceOrchestrationMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in ServiceOrchestrationMixin.__dict__
    assert method_name not in KraabUserbot.__dict__


def test_async_signatures_preserved():
    """Все extracted методы — sync (не coroutines)."""
    from src.userbot.service_orchestration import ServiceOrchestrationMixin

    for m in (
        "_ensure_maintenance_started",
        "_ensure_silence_schedule_started",
        "_ensure_memory_indexer_started",
        "_sync_scheduler_runtime",
    ):
        method = getattr(ServiceOrchestrationMixin, m)
        assert not inspect.iscoroutinefunction(method), f"{m} should be sync"


def test_kraab_userbot_full_mixin_set_after_wave_31_l():
    """13 mixins (Waves 31-A..L) подключены."""
    from src.userbot_bridge import KraabUserbot

    mro_names = [c.__name__ for c in KraabUserbot.__mro__ if c.__name__.endswith("Mixin")]
    assert "ServiceOrchestrationMixin" in mro_names
    # 19 baseline + 12 (A→L) = ~21+
    assert len(mro_names) >= 21


def test_ensure_maintenance_idempotent_when_task_active():
    """Если maintenance_task активна — повторный вызов no-op."""
    from unittest.mock import MagicMock

    from src.userbot.service_orchestration import ServiceOrchestrationMixin

    bot = ServiceOrchestrationMixin.__new__(ServiceOrchestrationMixin)
    fake_task = MagicMock()
    fake_task.done = MagicMock(return_value=False)
    bot.maintenance_task = fake_task

    bot._ensure_maintenance_started()
    # task не пересоздана — остался тот же
    assert bot.maintenance_task is fake_task
