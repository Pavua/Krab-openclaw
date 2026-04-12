# -*- coding: utf-8 -*-
"""
Тесты RuntimeStatusMixin для userbot.

Покрываем:
1) все _looks_like_*_question детекторы (все отключены → всегда False);
2) _build_runtime_model_status — форматирование маршрута по разным channel-значениям;
3) _resolve_runtime_access_mode — делегирует capability_registry;
4) _build_runtime_capability_status — owner/partial/guest ветки;
5) _build_runtime_commands_status — owner/partial/guest, scheduler-флаг;
6) _build_command_access_denied_text — тексты по access level;
7) get_runtime_state — структура словаря.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import src.userbot_bridge as userbot_bridge_module
from src.core.access_control import AccessLevel, AccessProfile
from src.userbot_bridge import KraabUserbot


def _make_bot_stub() -> KraabUserbot:
    """Минимальный bot stub для RuntimeStatusMixin без Pyrogram client."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.client = None
    bot._startup_state = "running"
    bot._startup_error_code = None
    bot._startup_error = None
    bot.router = None
    bot.voice_mode = False
    bot.voice_reply_speed = 1.5
    bot.voice_reply_voice = "ru-RU-DmitryNeural"
    bot.voice_reply_delivery = "text+voice"
    bot.perceptor = None
    return bot


# ---------------------------------------------------------------------------
# _looks_like_*_question — все отключены
# ---------------------------------------------------------------------------


def test_looks_like_model_status_question_always_false() -> None:
    """Детектор отключён по просьбе пользователя — должен вернуть False."""
    assert KraabUserbot._looks_like_model_status_question("на какой модели работаешь") is False
    assert KraabUserbot._looks_like_model_status_question("какая модель") is False
    assert KraabUserbot._looks_like_model_status_question("") is False


def test_looks_like_capability_status_question_always_false() -> None:
    """Детектор capabilities отключён — всегда False."""
    assert KraabUserbot._looks_like_capability_status_question("что ты умеешь") is False
    assert KraabUserbot._looks_like_capability_status_question("что можешь") is False


def test_looks_like_commands_question_always_false() -> None:
    """Детектор команд отключён — всегда False."""
    assert KraabUserbot._looks_like_commands_question("какие команды") is False
    assert KraabUserbot._looks_like_commands_question("список команд") is False


def test_looks_like_integrations_question_always_false() -> None:
    """Детектор интеграций отключён — всегда False."""
    assert KraabUserbot._looks_like_integrations_question("что подключено") is False
    assert KraabUserbot._looks_like_integrations_question("какие mcp") is False


def test_looks_like_runtime_truth_question_always_false() -> None:
    """Детектор self-check отключён — всегда False."""
    assert KraabUserbot._looks_like_runtime_truth_question("проверка связи") is False
    assert KraabUserbot._looks_like_runtime_truth_question("проведи диагностику") is False


# ---------------------------------------------------------------------------
# _build_runtime_model_status
# ---------------------------------------------------------------------------


def test_build_runtime_model_status_openclaw_cloud() -> None:
    """Для openclaw_cloud отображает channel, model, provider, tier."""
    route = {
        "channel": "openclaw_cloud",
        "model": "gemini-3.1",
        "provider": "google",
        "active_tier": "1",
    }
    txt = KraabUserbot._build_runtime_model_status(route)
    assert "openclaw_cloud" in txt
    assert "gemini-3.1" in txt
    assert "google" in txt
    assert "`1`" in txt


def test_build_runtime_model_status_local_direct_shows_lmstudio_label() -> None:
    """Для local_direct channel отображается подпись LM Studio."""
    route = {"channel": "local_direct", "model": "qwen", "provider": "lmstudio", "active_tier": "-"}
    txt = KraabUserbot._build_runtime_model_status(route)
    assert "local_direct (LM Studio)" in txt
    assert "qwen" in txt


def test_build_runtime_model_status_openclaw_local() -> None:
    """openclaw_local канал отображается без специальной подписи."""
    route = {
        "channel": "openclaw_local",
        "model": "llama",
        "provider": "lmstudio",
        "active_tier": "-",
    }
    txt = KraabUserbot._build_runtime_model_status(route)
    assert "openclaw_local" in txt


def test_build_runtime_model_status_unknown_channel_passthrough() -> None:
    """Неизвестный channel проходит как есть."""
    route = {"channel": "future_channel", "model": "x", "provider": "y", "active_tier": "-"}
    txt = KraabUserbot._build_runtime_model_status(route)
    assert "future_channel" in txt


# ---------------------------------------------------------------------------
# _resolve_runtime_access_mode
# ---------------------------------------------------------------------------


def test_resolve_access_mode_owner_sender_returns_full_or_owner(monkeypatch) -> None:
    """Разрешённый отправитель → owner или full, не guest."""
    bot = _make_bot_stub()
    mode = bot._resolve_runtime_access_mode(is_allowed_sender=True, access_level=None)
    assert mode in {AccessLevel.OWNER.value, AccessLevel.FULL.value}


def test_resolve_access_mode_partial() -> None:
    """access_level=partial → partial."""
    bot = _make_bot_stub()
    mode = bot._resolve_runtime_access_mode(is_allowed_sender=False, access_level="partial")
    assert mode == AccessLevel.PARTIAL.value


def test_resolve_access_mode_guest() -> None:
    """Неизвестный/незарегистрированный → guest."""
    bot = _make_bot_stub()
    mode = bot._resolve_runtime_access_mode(is_allowed_sender=False, access_level=None)
    assert mode == AccessLevel.GUEST.value


# ---------------------------------------------------------------------------
# _build_runtime_capability_status
# ---------------------------------------------------------------------------


def test_build_capability_status_owner_contains_search_command(monkeypatch) -> None:
    """Owner-контур включает упоминание !search и файловых инструментов."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    with patch.object(
        userbot_bridge_module.model_manager, "get_current_model", return_value="gemini"
    ):
        txt = bot._build_runtime_capability_status(is_allowed_sender=True)
    assert "!search" in txt
    assert "!voice" in txt


def test_build_capability_status_guest_hides_owner_tools(monkeypatch) -> None:
    """Гостевой контур не раскрывает owner-only инструменты."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    with patch.object(userbot_bridge_module.model_manager, "get_current_model", return_value=""):
        txt = bot._build_runtime_capability_status(is_allowed_sender=False)
    # Гость видит базовые способности, но не команды !voice, !ls и т.д.
    assert "!voice" not in txt
    assert "!ls" not in txt
    assert "Что я уже умею" in txt


def test_build_capability_status_partial_contains_search_but_no_admin(monkeypatch) -> None:
    """Partial-контур включает !search, но не admin-команды."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    with patch.object(userbot_bridge_module.model_manager, "get_current_model", return_value=""):
        txt = bot._build_runtime_capability_status(is_allowed_sender=False, access_level="partial")
    assert "!search" in txt
    assert "!voice" not in txt


# ---------------------------------------------------------------------------
# _build_runtime_commands_status
# ---------------------------------------------------------------------------


def test_build_commands_status_owner_contains_core_commands(monkeypatch) -> None:
    """Owner-контур перечисляет !status, !model, !search, !ls."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    txt = bot._build_runtime_commands_status(is_allowed_sender=True)
    assert "!status" in txt
    assert "!model" in txt
    assert "!search" in txt
    assert "!ls" in txt


def test_build_commands_status_scheduler_enabled_adds_remind(monkeypatch) -> None:
    """Если SCHEDULER_ENABLED=True, список команд содержит !remind."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", True, raising=False)
    txt = bot._build_runtime_commands_status(is_allowed_sender=True)
    assert "!remind" in txt


def test_build_commands_status_partial_shows_search_only(monkeypatch) -> None:
    """Partial-контур показывает только !status, !help, !search."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    txt = bot._build_runtime_commands_status(is_allowed_sender=False, access_level="partial")
    assert "!search" in txt
    assert "!status" in txt
    # Не раскрывает admin-команды
    assert "!model" not in txt
    assert "!restart" not in txt


def test_build_commands_status_guest_hides_service_commands(monkeypatch) -> None:
    """Гость видит только сообщение об ограничении, без перечня owner-команд."""
    bot = _make_bot_stub()
    monkeypatch.setattr(userbot_bridge_module.config, "SCHEDULER_ENABLED", False, raising=False)
    txt = bot._build_runtime_commands_status(is_allowed_sender=False)
    # Список owner-команд скрыт
    assert "!model" not in txt
    assert "!restart" not in txt
    assert "Что доступно" in txt


# ---------------------------------------------------------------------------
# _build_command_access_denied_text
# ---------------------------------------------------------------------------


def test_command_access_denied_partial_access() -> None:
    """Для partial выдаёт текст про режим частичного доступа."""
    bot = _make_bot_stub()
    profile = AccessProfile(level=AccessLevel.PARTIAL, source="test")
    txt = bot._build_command_access_denied_text("status", profile)
    assert "частичного доступа" in txt
    assert "!status" in txt


def test_command_access_denied_guest_access() -> None:
    """Для guest выдаёт текст про доверенный контур."""
    bot = _make_bot_stub()
    profile = AccessProfile(level=AccessLevel.GUEST, source="test")
    txt = bot._build_command_access_denied_text("ls", profile)
    assert "доверенному контуру" in txt
    assert "!ls" in txt


def test_command_access_denied_normalizes_command_name() -> None:
    """Имя команды нормализуется к нижнему регистру."""
    bot = _make_bot_stub()
    profile = AccessProfile(level=AccessLevel.GUEST, source="test")
    txt = bot._build_command_access_denied_text("STATUS", profile)
    assert "!status" in txt


# ---------------------------------------------------------------------------
# get_runtime_state
# ---------------------------------------------------------------------------


def test_get_runtime_state_structure(monkeypatch) -> None:
    """get_runtime_state возвращает словарь с обязательными ключами."""
    bot = _make_bot_stub()
    state = bot.get_runtime_state()
    for key in (
        "startup_state",
        "startup_error_code",
        "startup_error",
        "client_connected",
        "authorized_user",
        "authorized_user_id",
        "voice_profile",
        "translator_profile",
        "translator_session",
    ):
        assert key in state, f"ключ '{key}' отсутствует в get_runtime_state"


def test_get_runtime_state_client_connected_false_when_client_is_none() -> None:
    """client=None → client_connected=False."""
    bot = _make_bot_stub()
    bot.client = None
    state = bot.get_runtime_state()
    assert state["client_connected"] is False


def test_get_runtime_state_me_exposed() -> None:
    """authorized_user и authorized_user_id берутся из self.me."""
    bot = _make_bot_stub()
    bot.me = SimpleNamespace(id=12345, username="testowner")
    state = bot.get_runtime_state()
    assert state["authorized_user"] == "testowner"
    assert state["authorized_user_id"] == 12345
