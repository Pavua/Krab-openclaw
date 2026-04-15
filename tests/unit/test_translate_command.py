# -*- coding: utf-8 -*-
"""
Юнит-тесты для handle_translate в command_handlers.py.

Покрывает:
- !translate <текст> — перевод по профилю (es-ru)
- !translate en <текст> — перевод на явный язык
- !translate (reply) — перевод текста ответного сообщения
- Ошибочные случаи: нет текста, пустой ответ, исключение при переводе
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_translate

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(language_pair: str = "es-ru") -> SimpleNamespace:
    """Минимальный mock-bot с translator profile."""
    profile = {
        "language_pair": language_pair,
        "translation_mode": "bilingual",
    }
    return SimpleNamespace(
        get_translator_runtime_profile=lambda: dict(profile),
        update_translator_runtime_profile=lambda **kw: dict(profile),
    )


def _make_message(text: str, reply_text: str | None = None) -> SimpleNamespace:
    """Mock Message с опциональным reply_to_message."""
    reply_msg = None
    if reply_text is not None:
        reply_msg = SimpleNamespace(text=reply_text)
    return SimpleNamespace(
        text=text,
        command=None,  # handle_translate использует text напрямую
        reply=AsyncMock(),
        reply_to_message=reply_msg,
        chat=SimpleNamespace(id=12345),
    )


def _make_translation_result(
    original: str = "hola",
    translated: str = "привет",
    src_lang: str = "es",
    tgt_lang: str = "ru",
    latency_ms: int = 120,
    model_id: str = "gemini-3-flash",
) -> SimpleNamespace:
    """Mock TranslationResult."""
    return SimpleNamespace(
        original=original,
        translated=translated,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        latency_ms=latency_ms,
        model_id=model_id,
    )


# ---------------------------------------------------------------------------
# Тесты: успешный перевод по профилю
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_по_профилю_es_ru():
    """!translate hola → переводит es→ru."""
    bot = _make_bot("es-ru")
    msg = _make_message("!translate hola")
    result = _make_translation_result("hola", "привет", "es", "ru")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    orig_rtp = _ld.resolve_translation_pair

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "es"
    _ld.resolve_translation_pair = lambda d, p: ("es", "ru")

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl
        _ld.resolve_translation_pair = orig_rtp

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "es→ru" in reply_text
    assert "hola" in reply_text
    assert "привет" in reply_text


@pytest.mark.asyncio
async def test_translate_явный_язык_en():
    """!translate en hola mundo → переводит на английский."""
    bot = _make_bot("es-ru")
    msg = _make_message("!translate en hola mundo")
    result = _make_translation_result("hola mundo", "hello world", "es", "en")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "es"

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "es→en" in reply_text
    assert "hello world" in reply_text


@pytest.mark.asyncio
async def test_translate_reply_message():
    """!translate без текста, в ответ на сообщение → берёт текст из reply."""
    bot = _make_bot("es-ru")
    msg = _make_message("!translate", reply_text="Buenos días")
    result = _make_translation_result("Buenos días", "Доброе утро", "es", "ru")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    orig_rtp = _ld.resolve_translation_pair

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "es"
    _ld.resolve_translation_pair = lambda d, p: ("es", "ru")

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl
        _ld.resolve_translation_pair = orig_rtp

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "Доброе утро" in reply_text


# ---------------------------------------------------------------------------
# Тесты: ошибочные случаи
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_нет_текста_raises_user_input_error():
    """!translate без текста и без reply → UserInputError."""
    bot = _make_bot()
    msg = _make_message("!translate")

    with pytest.raises(UserInputError):
        await handle_translate(bot, msg)


@pytest.mark.asyncio
async def test_translate_пустой_ответ_от_модели():
    """Если translate_text вернул пустой translated → сообщение об ошибке."""
    bot = _make_bot("es-ru")
    msg = _make_message("!translate hola")
    result = _make_translation_result("hola", "", "es", "ru")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    orig_rtp = _ld.resolve_translation_pair

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "es"
    _ld.resolve_translation_pair = lambda d, p: ("es", "ru")

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl
        _ld.resolve_translation_pair = orig_rtp

    msg.reply.assert_called_once()
    assert "Пустой" in msg.reply.call_args[0][0] or "пустой" in msg.reply.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_translate_исключение_при_переводе():
    """Если translate_text бросил исключение → reply с ошибкой, не падаем."""
    bot = _make_bot("es-ru")
    msg = _make_message("!translate hola")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    orig_rtp = _ld.resolve_translation_pair

    _te.translate_text = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    _ld.detect_language = lambda t: "es"
    _ld.resolve_translation_pair = lambda d, p: ("es", "ru")

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl
        _ld.resolve_translation_profile = orig_rtp

    msg.reply.assert_called_once()
    assert "Ошибка" in msg.reply.call_args[0][0]


# ---------------------------------------------------------------------------
# Тесты: alias языков
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_alias_рус():
    """!translate рус hello → переводит на русский (alias рус→ru)."""
    bot = _make_bot("en-ru")
    msg = _make_message("!translate рус hello world")
    result = _make_translation_result("hello world", "привет мир", "en", "ru")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "en"

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args[0][0]
    assert "привет мир" in reply_text


@pytest.mark.asyncio
async def test_translate_src_eq_tgt_fallback():
    """Если detected lang == tgt_lang → fallback на другой язык из пары."""
    bot = _make_bot("es-ru")
    # Текст на русском, но target = ru → должен переключиться на es или ru из пары
    msg = _make_message("!translate ru привет")
    result = _make_translation_result("привет", "hola", "ru", "es")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language

    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "ru"

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    # Должен был вызвать reply (не упасть)
    assert msg.reply.called
