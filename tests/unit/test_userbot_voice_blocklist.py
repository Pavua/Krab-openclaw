# -*- coding: utf-8 -*-
"""
Регрессии per-chat voice blocklist на userbot.

Контекст:
В How2AI (chat_id -1001587432709) yung_nagato получил `USER_BANNED_IN_CHANNEL`
после того как админ/автомодерация пометили TTS-голосовые как спам. Чтобы такое
не повторялось в других группах, у Краба появился явный opt-out список чатов,
в которые он не шлёт voice (текст продолжает работать штатно).

Здесь проверяем именно бот-сторону:
1) `_is_voice_blocked_for_chat` читает `config.VOICE_REPLY_BLOCKED_CHATS` live,
   без кеша — иначе runtime-команды `!voice block` не будут применяться;
2) `add_voice_blocked_chat` / `remove_voice_blocked_chat` идемпотентны и
   обновляют и in-memory config, и persist на диск;
3) `get_voice_runtime_profile()` отдаёт `blocked_chats` в profile dict,
   чтобы renderer/owner UI видели актуальный список без лишних вызовов.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.userbot_bridge as userbot_bridge_module
from src.userbot_bridge import KraabUserbot


def _make_bot_stub() -> KraabUserbot:
    """Минимальный bot stub без Pyrogram client — только для voice blocklist API."""
    bot = KraabUserbot.__new__(KraabUserbot)
    bot.me = SimpleNamespace(id=777, username="owner")
    bot.voice_mode = True
    bot.voice_reply_speed = 1.5
    bot.voice_reply_voice = "ru-RU-DmitryNeural"
    bot.voice_reply_delivery = "text+voice"
    bot.perceptor = None
    return bot


@pytest.fixture(autouse=True)
def _reset_blocklist(monkeypatch):
    """
    Каждый тест видит пустой blocklist, даже если `.env` в репо его выставил.

    Важная тонкость: `test_config_voice_settings.py` в том же sweep делает
    `importlib.reload(src.config)`. После reload:
    - `src.config.config` — НОВЫЙ instance НОВОГО Config класса,
    - `userbot_bridge_module.config` (импортированный на старте) — СТАРЫЙ instance.

    `voice_profile.py` внутри методов делает `from ..config import config`, то есть
    читает **текущий** `src.config.config` (новый). Поэтому patch'ить надо этот
    live instance, а не `userbot_bridge_module.config.__class__` — иначе runtime
    читает один объект, а мы чистим другой, и состояние протекает между тестами.
    """
    import src.config as _config_module  # noqa: PLC0415

    live_cls = _config_module.config.__class__
    monkeypatch.setattr(live_cls, "VOICE_REPLY_BLOCKED_CHATS", [], raising=False)
    # Страхуемся: если инстанс shadow'ит class-атрибут через instance dict —
    # убираем, чтобы class-level patch не был проигнорирован.
    if "VOICE_REPLY_BLOCKED_CHATS" in _config_module.config.__dict__:
        monkeypatch.delattr(_config_module.config, "VOICE_REPLY_BLOCKED_CHATS")
    # update_setting() в этих тестах не должен трогать реальный .env — подменяем
    # на чистую in-memory реализацию, которая пишет только в classref.
    monkeypatch.setattr(
        live_cls,
        "update_setting",
        classmethod(
            lambda cls, key, value: (
                setattr(
                    cls,
                    "VOICE_REPLY_BLOCKED_CHATS",
                    [s.strip() for s in (value or "").split(",") if s.strip()],
                )
                or True
            )
        ),
    )
    yield


def test_blocklist_empty_by_default() -> None:
    bot = _make_bot_stub()
    assert bot.get_voice_blocked_chats() == []
    assert bot._is_voice_blocked_for_chat(-1001587432709) is False


def test_add_is_idempotent_and_persists_through_config_update() -> None:
    bot = _make_bot_stub()
    result1 = bot.add_voice_blocked_chat("-1001587432709")
    assert result1 == ["-1001587432709"]
    # Повторный add — noop, размер не меняется.
    result2 = bot.add_voice_blocked_chat("-1001587432709")
    assert result2 == ["-1001587432709"]
    # Config обновлён в памяти → _is_voice_blocked_for_chat видит это сразу.
    assert bot._is_voice_blocked_for_chat(-1001587432709) is True
    assert bot._is_voice_blocked_for_chat("-1001587432709") is True
    # Посторонний чат не задет.
    assert bot._is_voice_blocked_for_chat(-100999999) is False


def test_remove_is_idempotent_and_leaves_others_intact() -> None:
    bot = _make_bot_stub()
    bot.add_voice_blocked_chat("-1001587432709")
    bot.add_voice_blocked_chat("-1002000000000")
    assert set(bot.get_voice_blocked_chats()) == {
        "-1001587432709",
        "-1002000000000",
    }

    after_remove = bot.remove_voice_blocked_chat("-1001587432709")
    assert after_remove == ["-1002000000000"]
    # Повторный remove несуществующего — noop без исключения.
    after_noop = bot.remove_voice_blocked_chat("-1001587432709")
    assert after_noop == ["-1002000000000"]

    assert bot._is_voice_blocked_for_chat(-1001587432709) is False
    assert bot._is_voice_blocked_for_chat(-1002000000000) is True


def test_add_rejects_empty_chat_id() -> None:
    bot = _make_bot_stub()
    with pytest.raises(ValueError):
        bot.add_voice_blocked_chat("")
    with pytest.raises(ValueError):
        bot.add_voice_blocked_chat(None)


def test_profile_exposes_blocked_chats() -> None:
    bot = _make_bot_stub()
    bot.add_voice_blocked_chat("-1001587432709")
    profile = bot.get_voice_runtime_profile()
    assert profile["blocked_chats"] == ["-1001587432709"]
    # И рендерер, и handoff будут читать именно отсюда — поэтому ключ обязательный.
    assert "enabled" in profile
    assert "delivery" in profile


def test_live_config_reads_without_rebuild() -> None:
    """
    Ключевой инвариант: даже если owner правит `.env` вручную и потом вызывает
    `config.update_setting`, bot должен сразу это видеть без рестарта. Эмулируем
    прямой monkeypatch атрибута, как если бы это сделал dotenv reload.
    """
    import src.config as _config_module  # noqa: PLC0415

    bot = _make_bot_stub()
    # Читаем ровно тот instance, который `voice_profile.py` видит через
    # `from ..config import config` — иначе после `importlib.reload(src.config)`
    # в других тестах patch и runtime смотрят на разные Config-классы.
    target_cls = _config_module.config.__class__
    assert bot._is_voice_blocked_for_chat(-777) is False
    target_cls.VOICE_REPLY_BLOCKED_CHATS = ["-777"]
    try:
        assert bot._is_voice_blocked_for_chat(-777) is True
    finally:
        target_cls.VOICE_REPLY_BLOCKED_CHATS = []
    assert bot._is_voice_blocked_for_chat(-777) is False
