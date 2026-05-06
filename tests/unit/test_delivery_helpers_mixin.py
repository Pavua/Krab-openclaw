# -*- coding: utf-8 -*-
"""Wave 31-M tests: DeliveryHelpersMixin extraction."""

from __future__ import annotations

import inspect

import pytest


def test_delivery_helpers_mixin_importable():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    assert DeliveryHelpersMixin.__name__ == "DeliveryHelpersMixin"


def test_kraab_userbot_inherits_delivery_helpers_mixin():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin
    from src.userbot_bridge import KraabUserbot

    assert DeliveryHelpersMixin in KraabUserbot.__mro__


@pytest.mark.parametrize(
    "method_name",
    [
        "_should_force_cloud_for_photo_route",
        "_deliver_response_parts",
        "_maybe_record_smart_trigger_response",
        "_maybe_schedule_autodel",
        "_message_ids_from_delivery",
        "_build_effective_user_query",
    ],
)
def test_methods_resolve_via_mixin(method_name):
    from src.userbot.delivery_helpers import DeliveryHelpersMixin
    from src.userbot_bridge import KraabUserbot

    assert method_name in DeliveryHelpersMixin.__dict__
    assert method_name not in KraabUserbot.__dict__


def test_should_force_cloud_no_images_returns_false():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    assert DeliveryHelpersMixin._should_force_cloud_for_photo_route(has_images=False) is False


def test_should_force_cloud_with_images_default_true():
    """Default config.USERBOT_FORCE_CLOUD_FOR_PHOTO=True → has_images=True forces cloud."""
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    assert DeliveryHelpersMixin._should_force_cloud_for_photo_route(has_images=True) is True


def test_message_ids_from_delivery_handles_none():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    assert DeliveryHelpersMixin._message_ids_from_delivery(None) == []
    assert DeliveryHelpersMixin._message_ids_from_delivery({}) == []
    assert DeliveryHelpersMixin._message_ids_from_delivery({"text_message_ids": "not-list"}) == []


def test_message_ids_from_delivery_extracts_strings():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    result = DeliveryHelpersMixin._message_ids_from_delivery(
        {"text_message_ids": [123, "456", "  789 ", ""]}
    )
    assert result == ["123", "456", "789"]


def test_build_effective_query_normalizes_empty_with_images():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    result = DeliveryHelpersMixin._build_effective_user_query(query="", has_images=True)
    assert "Опиши присланное изображение" in result


def test_build_effective_query_with_reply_context():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    result = DeliveryHelpersMixin._build_effective_user_query(
        query="как там?",
        has_images=False,
        reply_context="контекст исходного",
    )
    assert "В ответ на сообщение" in result
    assert "как там?" in result


def test_build_effective_query_group_chat_adds_sender_prefix():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    result = DeliveryHelpersMixin._build_effective_user_query(
        query="привет",
        has_images=False,
        is_group=True,
        sender_name="alex",
    )
    assert result.startswith("[alex]:")


def test_deliver_response_parts_is_coroutine():
    from src.userbot.delivery_helpers import DeliveryHelpersMixin

    assert inspect.iscoroutinefunction(DeliveryHelpersMixin._deliver_response_parts)


def test_full_mixin_set_after_wave_31_m():
    from src.userbot_bridge import KraabUserbot

    mro_names = [c.__name__ for c in KraabUserbot.__mro__ if c.__name__.endswith("Mixin")]
    assert "DeliveryHelpersMixin" in mro_names
    assert len(mro_names) >= 22
