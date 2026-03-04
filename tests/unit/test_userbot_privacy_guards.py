# -*- coding: utf-8 -*-
"""
Тесты privacy-guard в userbot_bridge.

Покрываем:
1) очистку transport-тега `[[reply_to:...]]` из финального текста;
2) изоляцию runtime chat scope для неавторизованных пользователей;
3) safe prompt для не-owner контактов.
"""

from __future__ import annotations

from types import SimpleNamespace

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
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)
    prompt = bot._build_system_prompt_for_sender(is_allowed_sender=False)
    assert prompt == "SAFE_PROMPT_TEST"


def test_deferred_action_guard_adds_warning_when_scheduler_disabled(monkeypatch) -> None:
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "DEFERRED_ACTION_GUARD_ENABLED", True, raising=False)
    text = "Хорошо, сделаю это завтра утром по таймеру."
    guarded = KraabUserbot._apply_deferred_action_guard(text)
    assert "⚠️ Важно: фоновый cron/таймер сейчас не активен" in guarded


def test_deferred_action_guard_noop_when_scheduler_enabled(monkeypatch) -> None:
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "DEFERRED_ACTION_GUARD_ENABLED", True, raising=False)
    text = "Хорошо, сделаю это завтра утром по таймеру."
    guarded = KraabUserbot._apply_deferred_action_guard(text)
    assert guarded == text


def test_sync_scheduler_runtime_starts_when_enabled_and_connected(monkeypatch) -> None:
    """Scheduler должен запускаться при enabled + активном Telegram-клиенте."""
    bot = _make_bot_stub()
    bot.client = SimpleNamespace(is_connected=True)

    class _FakeScheduler:
        def __init__(self) -> None:
            self.is_started = False
            self.sender = None

        def bind_sender(self, sender) -> None:
            self.sender = sender

        def start(self) -> None:
            self.is_started = True

        def stop(self) -> None:
            self.is_started = False

    fake = _FakeScheduler()
    monkeypatch.setattr(userbot_bridge_module, "krab_scheduler", fake, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)

    bot._sync_scheduler_runtime()

    assert fake.is_started is True
    assert callable(fake.sender)


def test_sync_scheduler_runtime_stops_when_disabled(monkeypatch) -> None:
    """Scheduler должен останавливаться, если флаг выключен."""
    bot = _make_bot_stub()
    bot.client = SimpleNamespace(is_connected=True)

    class _FakeScheduler:
        def __init__(self) -> None:
            self.is_started = True

        def bind_sender(self, sender) -> None:
            return None

        def start(self) -> None:
            self.is_started = True

        def stop(self) -> None:
            self.is_started = False

    fake = _FakeScheduler()
    monkeypatch.setattr(userbot_bridge_module, "krab_scheduler", fake, raising=False)
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)

    bot._sync_scheduler_runtime()

    assert fake.is_started is False
