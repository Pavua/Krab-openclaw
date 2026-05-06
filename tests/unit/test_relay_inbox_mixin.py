# -*- coding: utf-8 -*-
"""Wave 31-F tests: RelayInboxMixin extraction validation."""

from __future__ import annotations

import inspect

import pytest


def test_relay_inbox_mixin_importable():
    from src.userbot.relay_inbox import RelayInboxMixin

    assert RelayInboxMixin.__name__ == "RelayInboxMixin"


def test_relay_intent_keywords_module_level():
    """Константа определена на уровне модуля + immutable."""
    from src.userbot.relay_inbox import _RELAY_INTENT_KEYWORDS

    assert isinstance(_RELAY_INTENT_KEYWORDS, frozenset)
    assert len(_RELAY_INTENT_KEYWORDS) >= 8
    # Sanity: ключевые keyword'ы присутствуют
    for kw in {"передай", "сообщи", "напомни", "tell pablo"}:
        assert kw in _RELAY_INTENT_KEYWORDS, f"missing keyword: {kw}"


def test_relay_intent_backward_compat_via_bridge():
    """tests/llm_flow используют ``from src.userbot_bridge import _RELAY_INTENT_KEYWORDS``."""
    from src.userbot.relay_inbox import _RELAY_INTENT_KEYWORDS as src_keywords
    from src.userbot_bridge import _RELAY_INTENT_KEYWORDS as bridge_keywords

    assert bridge_keywords is src_keywords, (
        "bridge re-export должен указывать на тот же frozenset (один источник истины)"
    )


def test_detect_relay_intent_positive():
    from src.userbot.relay_inbox import RelayInboxMixin

    assert RelayInboxMixin._detect_relay_intent("передай ему привет") is True
    assert RelayInboxMixin._detect_relay_intent("notify pablo about it") is True
    assert RelayInboxMixin._detect_relay_intent("сообщи владельцу") is True


def test_detect_relay_intent_negative():
    from src.userbot.relay_inbox import RelayInboxMixin

    assert RelayInboxMixin._detect_relay_intent("привет, как дела?") is False
    assert RelayInboxMixin._detect_relay_intent("") is False


def test_should_capture_skipped_when_self():
    from src.userbot.relay_inbox import RelayInboxMixin

    result = RelayInboxMixin._should_capture_incoming_owner_item(
        is_self=True,
        is_allowed_sender=True,
        chat_type="private",
        is_reply_to_me=False,
        has_trigger=False,
        has_photo=False,
        has_audio=False,
        query="hi",
    )
    assert result is False, "self-messages не должны попадать в inbox"


def test_should_capture_private_with_query():
    from src.userbot.relay_inbox import RelayInboxMixin

    assert RelayInboxMixin._should_capture_incoming_owner_item(
        is_self=False,
        is_allowed_sender=True,
        chat_type="private",
        is_reply_to_me=False,
        has_trigger=False,
        has_photo=False,
        has_audio=False,
        query="привет",
    )


def test_should_capture_group_requires_trigger():
    from src.userbot.relay_inbox import RelayInboxMixin

    # Без trigger / reply — group skip даже от allowed sender'а
    assert not RelayInboxMixin._should_capture_incoming_owner_item(
        is_self=False,
        is_allowed_sender=True,
        chat_type="supergroup",
        is_reply_to_me=False,
        has_trigger=False,
        has_photo=False,
        has_audio=False,
        query="хелло",
    )
    # Trigger ON — capture
    assert RelayInboxMixin._should_capture_incoming_owner_item(
        is_self=False,
        is_allowed_sender=True,
        chat_type="supergroup",
        is_reply_to_me=False,
        has_trigger=True,
        has_photo=False,
        has_audio=False,
        query="хелло",
    )


def test_kraab_userbot_inherits_relay_mixin():
    from src.userbot.relay_inbox import RelayInboxMixin
    from src.userbot_bridge import KraabUserbot

    assert RelayInboxMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_record_incoming_reply_to_inbox",
        "_should_capture_incoming_owner_item",
        "_acknowledge_open_relay_requests_for_chat",
        "_sync_incoming_message_to_inbox",
        "_detect_relay_intent",
        "_escalate_relay_to_owner",
        "_forward_guest_incoming_to_owner",
    ],
)
def test_relay_methods_resolve_via_mixin(method_name):
    """Все 7 методов резолвятся через RelayInboxMixin (не остались на bridge).

    Проверяем по RelayInboxMixin напрямую, чтобы избежать конфликта с autouse
    fixture ``isolate_userbot_inbox_capture`` (monkey-patches
    ``_sync_incoming_message_to_inbox`` на KraabUserbot — это нормальная test
    isolation, но даёт false-positive для нашего structural check'а).
    """
    from src.userbot.relay_inbox import RelayInboxMixin

    # Метод определён на mixin'е
    assert method_name in RelayInboxMixin.__dict__, (
        f"{method_name} отсутствует в RelayInboxMixin.__dict__"
    )


def test_async_relay_methods_are_coroutines():
    """_escalate_relay_to_owner и _forward_guest_incoming_to_owner должны быть async."""
    from src.userbot.relay_inbox import RelayInboxMixin

    for m in ("_escalate_relay_to_owner", "_forward_guest_incoming_to_owner"):
        method = getattr(RelayInboxMixin, m)
        assert inspect.iscoroutinefunction(method), f"{m} should be async"
