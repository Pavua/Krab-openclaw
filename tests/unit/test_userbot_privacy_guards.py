# -*- coding: utf-8 -*-
"""
Тесты privacy-guard в userbot_bridge.

Покрываем:
1) очистку transport-тега `[[reply_to:...]]` из финального текста;
2) изоляцию runtime chat scope для неавторизованных пользователей;
3) safe prompt для не-owner контактов.
"""

from __future__ import annotations

from src.userbot_bridge import KraabUserbot
import src.userbot_bridge as userbot_bridge_module


def _make_bot_stub() -> KraabUserbot:
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.current_role = "default"
    bot.me = None
    return bot


def test_strip_transport_markup_reply_to_tag() -> None:
    raw = "[[reply_to:696801]] Привет! [[reply_to:777]] Как дела?"
    cleaned = KraabUserbot._strip_transport_markup(raw)
    assert "[[reply_to:" not in cleaned
    assert "Привет!" in cleaned
    assert "Как дела?" in cleaned


def test_build_runtime_chat_scope_isolated_for_non_owner(monkeypatch) -> None:
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "NON_OWNER_SAFE_MODE_ENABLED", True, raising=False)
    scope = bot._build_runtime_chat_scope_id(chat_id="12345", user_id=777, is_allowed_sender=False)
    assert scope == "guest:12345:777"


def test_build_runtime_chat_scope_keeps_chat_for_owner(monkeypatch) -> None:
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "NON_OWNER_SAFE_MODE_ENABLED", True, raising=False)
    scope = bot._build_runtime_chat_scope_id(chat_id="12345", user_id=777, is_allowed_sender=True)
    assert scope == "12345"


def test_build_system_prompt_for_non_owner_uses_safe_prompt(monkeypatch) -> None:
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "NON_OWNER_SAFE_MODE_ENABLED", True, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "NON_OWNER_SAFE_PROMPT", "SAFE_PROMPT_TEST", raising=False)
    prompt = bot._build_system_prompt_for_sender(is_allowed_sender=False)
    assert prompt == "SAFE_PROMPT_TEST"
