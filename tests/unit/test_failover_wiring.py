# -*- coding: utf-8 -*-
"""
Тесты регистрации callback'ов `provider_failover` в `KraabUserbot._activate_provider_failover`.

Покрытие:
1. Apply-callback использует `set_primary_model` если оно есть.
2. Apply-callback падает на `config.update_setting("MODEL", ...)` fallback если
   ни set_primary_model, ни switch_model нет.
3. Notification-callback шлёт сообщение по owner-ID (или "me" если OWNER_USER_IDS пусто).
4. Graceful-fallback если `provider_failover` не импортируется — не поднимает.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_bot() -> MagicMock:
    """Создаёт mock KraabUserbot с методом `_activate_provider_failover`."""
    from src.userbot_bridge import KraabUserbot

    bot = MagicMock(spec=KraabUserbot)
    bot.client = MagicMock()
    bot.client.send_message = AsyncMock(return_value=MagicMock())
    # Биндим оригинальный метод — чтобы тестировать его тело, а не mock.
    bot._activate_provider_failover = KraabUserbot._activate_provider_failover.__get__(
        bot, KraabUserbot
    )
    return bot


@pytest.mark.asyncio
async def test_failover_apply_uses_set_primary_model(monkeypatch: pytest.MonkeyPatch):
    """Apply-callback предпочитает `openclaw_client.set_primary_model`."""
    import src.userbot_bridge as ub
    from src.core.provider_failover import ProviderFailoverPolicy

    # Инжектируем свежий policy singleton для изоляции теста.
    fresh_policy = ProviderFailoverPolicy()

    # openclaw_client должен иметь `set_primary_model`.
    calls: list[str] = []

    def _set_primary(model_id: str) -> None:
        calls.append(model_id)

    fake_client = SimpleNamespace(set_primary_model=_set_primary)
    monkeypatch.setattr(ub, "openclaw_client", fake_client)

    # Патчим `failover_policy` в свежий экземпляр.
    import src.core.provider_failover as pf_module

    monkeypatch.setattr(pf_module, "failover_policy", fresh_policy)

    bot = _make_bot()
    bot._activate_provider_failover()

    # Вызываем apply-callback руками — имитируем policy trigger.
    assert fresh_policy._failover_callback is not None
    await fresh_policy._failover_callback("codex-cli", "google/gemini-3-pro-preview")

    assert calls == ["google/gemini-3-pro-preview"]


@pytest.mark.asyncio
async def test_failover_apply_falls_back_to_config(monkeypatch: pytest.MonkeyPatch):
    """Если нет switch-методов — fallback на `config.update_setting("MODEL", ...)`."""
    import src.userbot_bridge as ub
    from src.core.provider_failover import ProviderFailoverPolicy

    fresh_policy = ProviderFailoverPolicy()

    fake_client = SimpleNamespace()  # ни set_primary_model, ни switch_model
    monkeypatch.setattr(ub, "openclaw_client", fake_client)

    import src.core.provider_failover as pf_module

    monkeypatch.setattr(pf_module, "failover_policy", fresh_policy)

    # Mock config.update_setting — фиксируем вызов.
    updates: list[tuple[str, str]] = []

    def _update(key: str, value: str) -> None:
        updates.append((key, value))

    monkeypatch.setattr(ub.config, "update_setting", _update)

    bot = _make_bot()
    bot._activate_provider_failover()

    await fresh_policy._failover_callback("codex-cli", "google/gemini-3-flash-preview")

    assert ("MODEL", "google/gemini-3-flash-preview") in updates


@pytest.mark.asyncio
async def test_failover_notify_sends_to_owner(monkeypatch: pytest.MonkeyPatch):
    """Notification-callback шлёт DM через self.client.send_message."""
    import src.userbot_bridge as ub
    from src.core.provider_failover import ProviderFailoverPolicy

    fresh_policy = ProviderFailoverPolicy()
    import src.core.provider_failover as pf_module

    monkeypatch.setattr(pf_module, "failover_policy", fresh_policy)

    # OWNER_USER_IDS пустой — должен упасть на "me".
    monkeypatch.setattr(ub.config, "OWNER_USER_IDS", [], raising=False)

    bot = _make_bot()
    bot._activate_provider_failover()

    assert fresh_policy._notification_callback is not None
    await fresh_policy._notification_callback("🔄 Auto-failover: codex-cli → gemini")

    bot.client.send_message.assert_awaited_once()
    args, _ = bot.client.send_message.await_args
    assert args[0] == "me"
    assert "Auto-failover" in args[1]


@pytest.mark.asyncio
async def test_failover_notify_uses_owner_id_when_set(monkeypatch: pytest.MonkeyPatch):
    """Если OWNER_USER_IDS задан — шлём по первому ID."""
    import src.userbot_bridge as ub
    from src.core.provider_failover import ProviderFailoverPolicy

    fresh_policy = ProviderFailoverPolicy()
    import src.core.provider_failover as pf_module

    monkeypatch.setattr(pf_module, "failover_policy", fresh_policy)

    monkeypatch.setattr(ub.config, "OWNER_USER_IDS", ["123456789"], raising=False)

    bot = _make_bot()
    bot._activate_provider_failover()

    await fresh_policy._notification_callback("test message")

    args, _ = bot.client.send_message.await_args
    assert args[0] == 123456789  # int, not str


def test_failover_activation_tolerates_missing_module(monkeypatch: pytest.MonkeyPatch):
    """Если `provider_failover` не импортируется — `_activate_provider_failover` не падает."""

    # Ломаем импорт provider_failover — симулируем legacy/rollback.
    # Сохраняем оригинал чтобы восстановить после теста.
    original = sys.modules.pop("src.core.provider_failover", None)

    class _BrokenFinder:
        def find_spec(self, name: str, path=None, target=None):
            if name == "src.core.provider_failover":
                raise ImportError("simulated missing module")
            return None

    finder = _BrokenFinder()
    sys.meta_path.insert(0, finder)

    try:
        bot = _make_bot()
        # Должен вернуться без exception, только залогировать warning.
        bot._activate_provider_failover()
    finally:
        sys.meta_path.remove(finder)
        if original is not None:
            sys.modules["src.core.provider_failover"] = original


@pytest.mark.asyncio
async def test_failover_notify_handles_send_error(monkeypatch: pytest.MonkeyPatch):
    """Notification-callback не должен поднимать, если send_message упал."""
    import src.userbot_bridge as ub
    from src.core.provider_failover import ProviderFailoverPolicy

    fresh_policy = ProviderFailoverPolicy()
    import src.core.provider_failover as pf_module

    monkeypatch.setattr(pf_module, "failover_policy", fresh_policy)
    monkeypatch.setattr(ub.config, "OWNER_USER_IDS", [], raising=False)

    bot = _make_bot()
    bot.client.send_message = AsyncMock(side_effect=RuntimeError("flood_wait"))
    bot._activate_provider_failover()

    # Не должен поднять — warning в лог.
    await fresh_policy._notification_callback("test")
