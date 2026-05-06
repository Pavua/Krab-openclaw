# -*- coding: utf-8 -*-
"""Wave 31-G/H: SwarmTeamClientsMixin + MediaProcessorsMixin extraction validation."""

from __future__ import annotations

import inspect

import pytest


# ─── Wave 31-G: SwarmTeamClientsMixin ────────────────────────────────────────


def test_swarm_team_clients_mixin_importable():
    from src.userbot.swarm_team_clients import SwarmTeamClientsMixin

    assert SwarmTeamClientsMixin.__name__ == "SwarmTeamClientsMixin"


def test_kraab_userbot_inherits_swarm_team_clients_mixin():
    from src.userbot.swarm_team_clients import SwarmTeamClientsMixin
    from src.userbot_bridge import KraabUserbot

    assert SwarmTeamClientsMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_start_swarm_team_clients",
        "_stop_swarm_team_clients",
        "_init_swarm_team_clients",
    ],
)
def test_swarm_methods_resolve_via_mixin(method_name):
    from src.userbot.swarm_team_clients import SwarmTeamClientsMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in SwarmTeamClientsMixin.__dict__
    assert method_name not in KraabUserbot.__dict__
    method = getattr(KraabUserbot, method_name)
    assert inspect.iscoroutinefunction(method)


# ─── Wave 31-H: MediaProcessorsMixin ─────────────────────────────────────────


def test_media_processors_mixin_importable():
    from src.userbot.media_processors import MediaProcessorsMixin

    assert MediaProcessorsMixin.__name__ == "MediaProcessorsMixin"


def test_kraab_userbot_inherits_media_processors_mixin():
    from src.userbot.media_processors import MediaProcessorsMixin
    from src.userbot_bridge import KraabUserbot

    assert MediaProcessorsMixin in KraabUserbot.__mro__


def test_media_class_constants_inherited():
    """_TEXT_EXTENSIONS, _DOC_MAX_BYTES, _DOC_INLINE_BYTES должны быть на классе."""
    from src.userbot.media_processors import MediaProcessorsMixin
    from src.userbot_bridge import KraabUserbot

    assert hasattr(KraabUserbot, "_TEXT_EXTENSIONS")
    assert hasattr(KraabUserbot, "_DOC_MAX_BYTES")
    assert hasattr(KraabUserbot, "_DOC_INLINE_BYTES")

    # Sanity: размеры разумные
    assert KraabUserbot._DOC_MAX_BYTES == 5 * 1024 * 1024
    assert KraabUserbot._DOC_INLINE_BYTES == 80 * 1024
    assert isinstance(KraabUserbot._TEXT_EXTENSIONS, frozenset)
    # Несколько ключевых extensions
    for ext in (".py", ".md", ".json", ".txt", ".yaml"):
        assert ext in MediaProcessorsMixin._TEXT_EXTENSIONS


@pytest.mark.parametrize(
    "method_name",
    [
        "_process_document_message",
        "_describe_video_frame",
        "_process_video_message",
    ],
)
def test_media_methods_resolve_via_mixin(method_name):
    from src.userbot.media_processors import MediaProcessorsMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in MediaProcessorsMixin.__dict__
    assert method_name not in KraabUserbot.__dict__
    method = getattr(KraabUserbot, method_name)
    assert inspect.iscoroutinefunction(method)


def test_describe_video_frame_handles_empty_bytes():
    """Empty frame_bytes → fast-return пустой строкой (без сетевых вызовов)."""
    import asyncio

    from src.userbot.media_processors import MediaProcessorsMixin

    # Создаём минимальный объект-обёртку без полной инициализации KraabUserbot
    class _Stub(MediaProcessorsMixin):
        pass

    stub = _Stub()
    result = asyncio.run(stub._describe_video_frame(b"", 0, chat_id="test"))
    assert result == ""


def test_kraab_userbot_mro_order():
    """Все 9 mixins (Waves 31-A..H) подключены, порядок не сломан."""
    from src.userbot_bridge import KraabUserbot

    mro_names = [c.__name__ for c in KraabUserbot.__mro__]
    expected_mixins = {
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
    }
    missing = expected_mixins - set(mro_names)
    assert not missing, f"missing mixins in MRO: {missing}"
