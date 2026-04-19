# -*- coding: utf-8 -*-
"""
Юнит-тесты для handle_translator в command_handlers.py.
Покрываем все подкоманды через mock Message и mock bot (KraabUserbot).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_translator

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(
    language_pair: str = "es-ru",
    session_status: str = "idle",
    quick_phrases: list | None = None,
) -> SimpleNamespace:
    """Создаёт минимальный mock-bot с методами translator."""
    profile: dict = {
        "language_pair": language_pair,
        "translation_mode": "bilingual",
        "voice_strategy": "voice-first",
        "target_device": "iphone_companion",
        "ordinary_calls_enabled": True,
        "internet_calls_enabled": True,
        "subtitles_enabled": True,
        "timeline_enabled": True,
        "summary_enabled": True,
        "diagnostics_enabled": True,
        "quick_phrases": quick_phrases or [],
    }
    session: dict = {
        "session_status": session_status,
        "active_chats": [],
        "translation_muted": False,
        "active_session_label": "",
        "last_translated_original": "",
        "last_translated_translation": "",
        "last_language_pair": language_pair,
        "last_event": "",
        "stats": {"total_translations": 5, "total_latency_ms": 2500},
    }

    def _get_profile():
        return dict(profile)

    def _update_profile(**kwargs):
        profile.update(kwargs)
        return dict(profile)

    def _get_session():
        return dict(session)

    def _update_session(**kwargs):
        session.update(kwargs)
        return dict(session)

    return SimpleNamespace(
        get_translator_runtime_profile=_get_profile,
        update_translator_runtime_profile=_update_profile,
        get_translator_session_state=_get_session,
        update_translator_session_state=_update_session,
    )


def _make_message(command_args: list[str], text: str = "", chat_id: int = 12345) -> SimpleNamespace:
    """Создаёт mock Message с заполненным command и text."""
    return SimpleNamespace(
        command=command_args,
        text=text or " ".join(command_args),
        reply=AsyncMock(),
        chat=SimpleNamespace(id=chat_id),
    )


# ---------------------------------------------------------------------------
# Тесты: !translator (без аргументов) / !translator status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_без_аргументов_показывает_профиль():
    """!translator без аргументов → рендерит текущий профиль."""
    bot = _make_bot(language_pair="es-ru")
    msg = _make_message(["translator"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    текст = msg.reply.call_args[0][0]
    assert "es-ru" in текст


@pytest.mark.asyncio
async def test_translator_status_явно_показывает_профиль():
    """!translator status → то же что и без аргументов."""
    bot = _make_bot()
    msg = _make_message(["translator", "status"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    assert "es-ru" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тесты: !translator help
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_help_возвращает_список_команд():
    """!translator help → выводит список команд."""
    bot = _make_bot()
    msg = _make_message(["translator", "help"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    текст = msg.reply.call_args[0][0]
    assert "session" in текст
    assert "lang" in текст
    assert "test" in текст


# ---------------------------------------------------------------------------
# Тесты: !translator lang
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_lang_без_аргумента_показывает_текущую_пару():
    """!translator lang без значения → показывает текущую пару."""
    bot = _make_bot(language_pair="en-ru")
    msg = _make_message(["translator", "lang"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    assert "en-ru" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_translator_lang_меняет_языковую_пару():
    """!translator lang es-en → обновляет language_pair."""
    bot = _make_bot()
    msg = _make_message(["translator", "lang", "es-en"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    assert bot.get_translator_runtime_profile()["language_pair"] == "es-en"


@pytest.mark.asyncio
async def test_translator_lang_auto_detect_устанавливается():
    """!translator lang auto-detect → принимается как валидное значение."""
    bot = _make_bot()
    msg = _make_message(["translator", "lang", "auto-detect"])
    await handle_translator(bot, msg)
    assert bot.get_translator_runtime_profile()["language_pair"] == "auto-detect"


@pytest.mark.asyncio
async def test_translator_lang_невалидная_пара_вызывает_ошибку():
    """!translator lang xx-yy → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "lang", "xx-yy"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: !translator auto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_auto_устанавливает_auto_detect():
    """!translator auto → language_pair = auto-detect."""
    bot = _make_bot(language_pair="es-ru")
    msg = _make_message(["translator", "auto"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    assert bot.get_translator_runtime_profile()["language_pair"] == "auto-detect"
    assert "auto-detect" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тесты: !translator mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_mode_bilingual_устанавливается():
    """!translator mode bilingual → обновляет translation_mode."""
    bot = _make_bot()
    msg = _make_message(["translator", "mode", "bilingual"])
    await handle_translator(bot, msg)
    assert bot.get_translator_runtime_profile()["translation_mode"] == "bilingual"


@pytest.mark.asyncio
async def test_translator_mode_без_аргумента_вызывает_ошибку():
    """!translator mode без значения → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "mode"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


@pytest.mark.asyncio
async def test_translator_mode_невалидный_вызывает_ошибку():
    """!translator mode unknown → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "mode", "unknown"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: !translator strategy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_strategy_voice_first_устанавливается():
    """!translator strategy voice-first → обновляет voice_strategy."""
    bot = _make_bot()
    msg = _make_message(["translator", "strategy", "voice-first"])
    await handle_translator(bot, msg)
    assert bot.get_translator_runtime_profile()["voice_strategy"] == "voice-first"


@pytest.mark.asyncio
async def test_translator_strategy_невалидная_вызывает_ошибку():
    """!translator strategy bad → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "strategy", "bad"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: !translator session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_session_start_активирует_сессию():
    """!translator session start → session_status = active."""
    bot = _make_bot(session_status="idle")
    msg = _make_message(["translator", "session", "start"], chat_id=99)
    await handle_translator(bot, msg)
    state = bot.get_translator_session_state()
    assert state["session_status"] == "active"
    assert "99" in state["active_chats"]


@pytest.mark.asyncio
async def test_translator_session_pause_останавливает_на_паузу():
    """!translator session pause → session_status = paused."""
    bot = _make_bot(session_status="active")
    msg = _make_message(["translator", "session", "pause"])
    await handle_translator(bot, msg)
    assert bot.get_translator_session_state()["session_status"] == "paused"


@pytest.mark.asyncio
async def test_translator_session_resume_возобновляет():
    """!translator session resume → session_status = active."""
    bot = _make_bot(session_status="paused")
    msg = _make_message(["translator", "session", "resume"])
    await handle_translator(bot, msg)
    assert bot.get_translator_session_state()["session_status"] == "active"


@pytest.mark.asyncio
async def test_translator_session_stop_устанавливает_idle():
    """!translator session stop (один чат) → session_status = idle."""
    bot = _make_bot(session_status="active")
    # Предзаполняем active_chats с нашим чатом
    bot.update_translator_session_state(active_chats=["55"], session_status="active")
    msg = _make_message(["translator", "session", "stop"], chat_id=55)
    await handle_translator(bot, msg)
    state = bot.get_translator_session_state()
    assert state["session_status"] == "idle"
    assert state["active_chats"] == []


@pytest.mark.asyncio
async def test_translator_session_status_возвращает_состояние():
    """!translator session status → рендерит session state."""
    bot = _make_bot(session_status="paused")
    msg = _make_message(["translator", "session", "status"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()


# ---------------------------------------------------------------------------
# Тесты: !translator phrase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_phrase_add_добавляет_фразу():
    """!translator phrase add <текст> → фраза добавляется в список."""
    bot = _make_bot()
    msg = _make_message(["translator", "phrase", "add", "Повтори пожалуйста"])
    await handle_translator(bot, msg)
    phrases = bot.get_translator_runtime_profile()["quick_phrases"]
    assert "Повтори пожалуйста" in phrases


@pytest.mark.asyncio
async def test_translator_phrase_remove_удаляет_фразу():
    """!translator phrase remove 1 → удаляет первую фразу."""
    bot = _make_bot(quick_phrases=["Фраза один", "Фраза два"])
    msg = _make_message(["translator", "phrase", "remove", "1"])
    await handle_translator(bot, msg)
    phrases = bot.get_translator_runtime_profile()["quick_phrases"]
    assert "Фраза один" not in phrases
    assert "Фраза два" in phrases


@pytest.mark.asyncio
async def test_translator_phrase_remove_невалидный_индекс_ошибка():
    """!translator phrase remove 99 → UserInputError (нет такого номера)."""
    bot = _make_bot(quick_phrases=["Одна фраза"])
    msg = _make_message(["translator", "phrase", "remove", "99"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


# ---------------------------------------------------------------------------
# Тесты: !translator reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_reset_сбрасывает_профиль_к_дефолту():
    """!translator reset → профиль сбрасывается, ответ содержит профиль."""
    bot = _make_bot(language_pair="en-ru")
    # Меняем пару вручную
    bot.update_translator_runtime_profile(language_pair="es-en")
    msg = _make_message(["translator", "reset"])
    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    # Дефолтный профиль должен содержать es-ru
    текст = msg.reply.call_args[0][0]
    assert "es-ru" in текст


# ---------------------------------------------------------------------------
# Тесты: !translator test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_test_без_текста_вызывает_ошибку():
    """!translator test без текста → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "test"], text="!translator test")
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)


@pytest.mark.asyncio
async def test_translator_test_с_текстом_вызывает_перевод(monkeypatch):
    """!translator test Buenos días → вызывает перевод и отвечает."""
    bot = _make_bot()
    msg = _make_message(
        ["translator", "test", "Buenos días"],
        text="!translator test Buenos días",
    )

    # Мокаем модули языкового движка
    import src.handlers.command_handlers as ch_mod

    fake_result = SimpleNamespace(
        original="Buenos días",
        translated="Добрый день",
        latency_ms=120,
    )

    async def _fake_translate(text, src, tgt, openclaw_client=None):
        return fake_result

    import sys
    import types

    # Создаём fake-модули для импорта внутри функции
    fake_lang_mod = types.ModuleType("src.core.language_detect")
    fake_lang_mod.detect_language = lambda text: "es"
    fake_lang_mod.resolve_translation_pair = lambda detected, pair: ("es", "ru")

    fake_engine_mod = types.ModuleType("src.core.translator_engine")
    fake_engine_mod.translate_text = _fake_translate

    monkeypatch.setitem(sys.modules, "src.core.language_detect", fake_lang_mod)
    monkeypatch.setitem(sys.modules, "src.core.translator_engine", fake_engine_mod)

    # Мокаем openclaw_client
    monkeypatch.setattr(ch_mod, "openclaw_client", MagicMock(), raising=False)

    await handle_translator(bot, msg)
    msg.reply.assert_called_once()
    текст = msg.reply.call_args[0][0]
    assert "Добрый день" in текст or "Buenos días" in текст


# ---------------------------------------------------------------------------
# Тест: неизвестная подкоманда
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translator_неизвестная_подкоманда_вызывает_ошибку():
    """!translator foobar → UserInputError."""
    bot = _make_bot()
    msg = _make_message(["translator", "foobar"])
    with pytest.raises(UserInputError):
        await handle_translator(bot, msg)
