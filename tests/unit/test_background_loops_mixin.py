# -*- coding: utf-8 -*-
"""Wave 31-I tests: BackgroundLoopsMixin extraction validation."""

from __future__ import annotations

import inspect

import pytest


def test_background_loops_mixin_importable():
    from src.userbot.background_loops import BackgroundLoopsMixin

    assert BackgroundLoopsMixin.__name__ == "BackgroundLoopsMixin"


def test_kraab_userbot_inherits_background_loops_mixin():
    from src.userbot.background_loops import BackgroundLoopsMixin
    from src.userbot_bridge import KraabUserbot

    assert BackgroundLoopsMixin in KraabUserbot.__mro__


def test_evaluate_skill_curator_proposals_module_level_re_export():
    """tests могут импортировать `from src.userbot_bridge import _evaluate_...`"""
    from src.userbot.background_loops import (
        _evaluate_and_apply_skill_curator_proposals as src_fn,
    )
    from src.userbot_bridge import (
        _evaluate_and_apply_skill_curator_proposals as bridge_fn,
    )

    # Re-export должен указывать на ту же функцию (один источник истины)
    assert bridge_fn is src_fn, "bridge re-export должен биться 1:1 с mixin module"


@pytest.mark.parametrize(
    "method_name",
    ["_idea_features_tick_loop", "_command_usage_save_loop"],
)
def test_loop_methods_resolve_via_mixin(method_name):
    from src.userbot.background_loops import BackgroundLoopsMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in BackgroundLoopsMixin.__dict__
    assert method_name not in KraabUserbot.__dict__
    method = getattr(KraabUserbot, method_name)
    assert inspect.iscoroutinefunction(method)


def test_skill_curator_eval_is_coroutine():
    """_evaluate_and_apply_skill_curator_proposals — async."""
    from src.userbot.background_loops import _evaluate_and_apply_skill_curator_proposals

    assert inspect.iscoroutinefunction(_evaluate_and_apply_skill_curator_proposals)


def test_kraab_userbot_full_mixin_set_after_wave_31_i():
    """11 mixins (Waves 31-A..I) подключены."""
    from src.userbot_bridge import KraabUserbot

    mro_names = [c.__name__ for c in KraabUserbot.__mro__]
    expected = {
        "StartupStateMixin",
        "CallbackHandlerMixin",
        "NetworkWatchdogMixin",
        "TranslatorProfileMixin",
        "TelegramSendUtilsMixin",
        "ReactionDispatchMixin",
        "CronTaskMixin",
        "RelayInboxMixin",
        "SwarmTeamClientsMixin",
        "MediaProcessorsMixin",
        "BackgroundLoopsMixin",
    }
    missing = expected - set(mro_names)
    assert not missing, f"missing: {missing}"
