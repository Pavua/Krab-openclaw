# -*- coding: utf-8 -*-
"""
Тесты для улучшений !translate:
- auto_detect_direction (language_detect.py)
- автоопределение в handle_translate без профиля
- handle_translate_auto (toggle per-chat)
- AutoTranslateMixin (add/remove/is_enabled/get_list)
- _handle_auto_translate_message (fire-and-forget перевод входящих)
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Тесты: auto_detect_direction
# ---------------------------------------------------------------------------


class TestAutoDetectDirection:
    """Тесты правил автоопределения направления перевода."""

    def test_ru_to_en(self) -> None:
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("ru")
        assert src == "ru"
        assert tgt == "en"

    def test_en_to_ru(self) -> None:
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("en")
        assert src == "en"
        assert tgt == "ru"

    def test_es_to_ru(self) -> None:
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("es")
        assert src == "es"
        assert tgt == "ru"

    def test_fr_to_ru(self) -> None:
        """Французский — любой неизвестный → ru."""
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("fr")
        assert src == "fr"
        assert tgt == "ru"

    def test_de_to_ru(self) -> None:
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("de")
        assert src == "de"
        assert tgt == "ru"

    def test_pt_to_ru(self) -> None:
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("pt")
        assert src == "pt"
        assert tgt == "ru"

    def test_uk_to_ru(self) -> None:
        """Украинский → русский."""
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("uk")
        assert src == "uk"
        assert tgt == "ru"

    def test_unknown_to_ru(self) -> None:
        """Любой неизвестный код → ru."""
        from src.core.language_detect import auto_detect_direction

        src, tgt = auto_detect_direction("zh")
        assert src == "zh"
        assert tgt == "ru"

    def test_never_src_eq_tgt(self) -> None:
        """src никогда не равен tgt."""
        from src.core.language_detect import auto_detect_direction

        for lang in ["ru", "en", "es", "fr", "de", "it", "pt", "uk", "zh", "ja"]:
            src, tgt = auto_detect_direction(lang)
            assert src != tgt, f"src==tgt для lang={lang}"


# ---------------------------------------------------------------------------
# Тесты: handle_translate без профиля (автоопределение)
# ---------------------------------------------------------------------------


def _make_bot(language_pair: str = "") -> SimpleNamespace:
    """Mock-bot с translator profile (пустая пара = нет профиля)."""
    profile = {"language_pair": language_pair}
    return SimpleNamespace(
        get_translator_runtime_profile=lambda: dict(profile),
        is_auto_translate_enabled=lambda cid: False,
        add_auto_translate_chat=lambda cid: None,
        remove_auto_translate_chat=lambda cid: None,
    )


def _make_message(
    text: str, reply_text: str | None = None, chat_id: int = 12345
) -> SimpleNamespace:
    reply_msg = None
    if reply_text is not None:
        reply_msg = SimpleNamespace(text=reply_text)
    return SimpleNamespace(
        text=text,
        reply=AsyncMock(),
        reply_to_message=reply_msg,
        chat=SimpleNamespace(id=chat_id),
    )


def _make_result(
    original="hello", translated="привет", src_lang="en", tgt_lang="ru", latency_ms=100
):
    return SimpleNamespace(
        original=original,
        translated=translated,
        src_lang=src_lang,
        tgt_lang=tgt_lang,
        latency_ms=latency_ms,
        model_id="gemini-flash",
    )


@pytest.mark.asyncio
async def test_translate_auto_detect_ru_to_en():
    """Без профиля: русский текст → en."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    msg = _make_message("!translate Привет как дела")
    result = _make_result("Привет как дела", "Hello how are you", "ru", "en")

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

    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "ru→en" in reply_text


@pytest.mark.asyncio
async def test_translate_auto_detect_en_to_ru():
    """Без профиля: английский текст → ru."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    msg = _make_message("!translate Hello how are you")
    result = _make_result("Hello how are you", "Привет как дела", "en", "ru")

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

    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "en→ru" in reply_text


@pytest.mark.asyncio
async def test_translate_auto_detect_es_to_ru():
    """Без профиля: испанский текст → ru."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    msg = _make_message("!translate Buenos días")
    result = _make_result("Buenos días", "Добрый день", "es", "ru")

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

    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "es→ru" in reply_text


@pytest.mark.asyncio
async def test_translate_with_profile_pair():
    """С профилем es-ru: испанский → ru (через resolve_translation_pair)."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("es-ru")
    msg = _make_message("!translate hola")
    result = _make_result("hola", "привет", "es", "ru")

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

    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "es→ru" in reply_text


@pytest.mark.asyncio
async def test_translate_auto_detect_fallback_on_failed_detect():
    """Если detect_language не смог определить язык — fallback auto→ru, reply вызван."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    msg = _make_message("!translate abc")
    result = _make_result("abc", "абс", "auto", "ru")

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    mock_translate = AsyncMock(return_value=result)
    _te.translate_text = mock_translate
    _ld.detect_language = lambda t: ""  # не смогли определить

    try:
        await handle_translate(bot, msg)
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    # translate_text вызван с src=auto, tgt=ru
    assert mock_translate.called
    call_args = mock_translate.call_args
    assert call_args[0][1] == "auto"  # src_lang
    assert call_args[0][2] == "ru"  # tgt_lang
    assert msg.reply.called


@pytest.mark.asyncio
async def test_translate_auto_subcommand_dispatches():
    """!translate auto вызывает handle_translate_auto."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    bot.is_auto_translate_enabled = lambda cid: False
    bot.add_auto_translate_chat = MagicMock()
    msg = _make_message("!translate auto")

    await handle_translate(bot, msg)

    # reply должен быть вызван с сообщением о включении
    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "включён" in reply_text.lower() or "авто" in reply_text.lower()


@pytest.mark.asyncio
async def test_translate_авто_subcommand_dispatches():
    """!translate авто (русский alias) тоже вызывает handle_translate_auto."""
    from src.handlers.command_handlers import handle_translate

    bot = _make_bot("")
    bot.is_auto_translate_enabled = lambda cid: True  # уже включён → выключит
    bot.remove_auto_translate_chat = MagicMock()
    msg = _make_message("!translate авто")

    await handle_translate(bot, msg)

    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "выключен" in reply_text.lower()


# ---------------------------------------------------------------------------
# Тесты: handle_translate_auto
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_translate_auto_enable():
    """!translate auto включает автоперевод если ещё не включён."""
    from src.handlers.command_handlers import handle_translate_auto

    bot = _make_bot("")
    bot.is_auto_translate_enabled = lambda cid: False
    bot.add_auto_translate_chat = MagicMock()
    msg = _make_message("!translate auto", chat_id=99999)

    await handle_translate_auto(bot, msg)

    bot.add_auto_translate_chat.assert_called_once_with("99999")
    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "включён" in reply_text


@pytest.mark.asyncio
async def test_translate_auto_disable():
    """!translate auto выключает автоперевод если уже включён."""
    from src.handlers.command_handlers import handle_translate_auto

    bot = _make_bot("")
    bot.is_auto_translate_enabled = lambda cid: True
    bot.remove_auto_translate_chat = MagicMock()
    msg = _make_message("!translate auto", chat_id=77777)

    await handle_translate_auto(bot, msg)

    bot.remove_auto_translate_chat.assert_called_once_with("77777")
    assert msg.reply.called
    reply_text = msg.reply.call_args[0][0]
    assert "выключен" in reply_text


# ---------------------------------------------------------------------------
# Тесты: AutoTranslateMixin
# ---------------------------------------------------------------------------


class _BotWithMixin:
    """Минимальный bot для тестирования AutoTranslateMixin в изоляции."""

    pass


def _make_mixin_bot(tmp_path: Path):
    """Создаёт экземпляр AutoTranslateMixin с tmp state dir."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

    return TestBot()


def test_mixin_initially_empty(tmp_path):
    """Новый экземпляр — автоперевод нигде не включён."""
    bot = _make_mixin_bot(tmp_path)
    assert not bot.is_auto_translate_enabled("123")
    assert bot.get_auto_translate_chats() == []


def test_mixin_add_chat(tmp_path):
    """add_auto_translate_chat включает чат."""
    bot = _make_mixin_bot(tmp_path)
    bot.add_auto_translate_chat("456")
    assert bot.is_auto_translate_enabled("456")
    assert "456" in bot.get_auto_translate_chats()


def test_mixin_add_chat_idempotent(tmp_path):
    """Повторное добавление — без дублей."""
    bot = _make_mixin_bot(tmp_path)
    bot.add_auto_translate_chat("789")
    bot.add_auto_translate_chat("789")
    assert bot.get_auto_translate_chats().count("789") == 1


def test_mixin_remove_chat(tmp_path):
    """remove_auto_translate_chat выключает чат."""
    bot = _make_mixin_bot(tmp_path)
    bot.add_auto_translate_chat("100")
    bot.remove_auto_translate_chat("100")
    assert not bot.is_auto_translate_enabled("100")


def test_mixin_remove_not_existing_idempotent(tmp_path):
    """Удаление несуществующего чата не кидает исключение."""
    bot = _make_mixin_bot(tmp_path)
    bot.remove_auto_translate_chat("nonexistent")  # не должно упасть
    assert not bot.is_auto_translate_enabled("nonexistent")


def test_mixin_persist_to_disk(tmp_path):
    """Список чатов сохраняется на диск и загружается заново."""
    bot1 = _make_mixin_bot(tmp_path)
    bot1.add_auto_translate_chat("111")
    bot1.add_auto_translate_chat("222")

    # Создаём новый экземпляр — должен загрузить с диска
    bot2 = _make_mixin_bot(tmp_path)
    assert bot2.is_auto_translate_enabled("111")
    assert bot2.is_auto_translate_enabled("222")


def test_mixin_persist_after_remove(tmp_path):
    """После удаления чата — он не появляется в новом экземпляре."""
    bot1 = _make_mixin_bot(tmp_path)
    bot1.add_auto_translate_chat("333")
    bot1.remove_auto_translate_chat("333")

    bot2 = _make_mixin_bot(tmp_path)
    assert not bot2.is_auto_translate_enabled("333")


def test_mixin_corrupted_file_safe(tmp_path):
    """Битый JSON-файл — молча деградируем к пустому состоянию."""
    state_file = tmp_path / "auto_translate_chats.json"
    state_file.write_text("NOT VALID JSON", encoding="utf-8")

    bot = _make_mixin_bot(tmp_path)
    assert bot.get_auto_translate_chats() == []


def test_mixin_state_file_not_list(tmp_path):
    """Если в файле не список — молча деградируем."""
    state_file = tmp_path / "auto_translate_chats.json"
    state_file.write_text(json.dumps({"chat": "123"}), encoding="utf-8")

    bot = _make_mixin_bot(tmp_path)
    assert bot.get_auto_translate_chats() == []


def test_mixin_chat_id_as_int(tmp_path):
    """Числовой chat_id корректно преобразуется к str."""
    bot = _make_mixin_bot(tmp_path)
    bot.add_auto_translate_chat(12345)
    assert bot.is_auto_translate_enabled(12345)
    assert bot.is_auto_translate_enabled("12345")


# ---------------------------------------------------------------------------
# Тесты: _handle_auto_translate_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_auto_translate_message_translates(tmp_path):
    """Входящее сообщение переводится если автоперевод включён."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

        async def _safe_reply_or_send_new(self, message, text):
            self._sent_text = text

    bot = TestBot()
    bot.add_auto_translate_chat("chat_42")

    msg = SimpleNamespace(chat=SimpleNamespace(id=42))
    result = SimpleNamespace(
        translated="Hello",
        src_lang="ru",
        tgt_lang="en",
        latency_ms=80,
        original="Привет",
    )

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    _te.translate_text = AsyncMock(return_value=result)
    _ld.detect_language = lambda t: "ru"

    try:
        handled = await bot._handle_auto_translate_message(msg, "Привет", "chat_42")
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    assert handled is True
    assert "ru→en" in bot._sent_text
    assert "Hello" in bot._sent_text


@pytest.mark.asyncio
async def test_handle_auto_translate_message_skips_if_not_enabled(tmp_path):
    """Если автоперевод не включён — возвращает False."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

    bot = TestBot()  # никаких чатов не добавлено
    msg = SimpleNamespace()

    handled = await bot._handle_auto_translate_message(msg, "Привет", "chat_99")
    assert handled is False


@pytest.mark.asyncio
async def test_handle_auto_translate_message_skips_short_text(tmp_path):
    """Слишком короткий текст — не переводим."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

    bot = TestBot()
    bot.add_auto_translate_chat("chat_1")
    msg = SimpleNamespace()

    handled = await bot._handle_auto_translate_message(msg, "ok", "chat_1")
    assert handled is False


@pytest.mark.asyncio
async def test_handle_auto_translate_message_skips_same_language(tmp_path):
    """Если src == tgt — не переводим (возвращаем False)."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

    bot = TestBot()
    bot.add_auto_translate_chat("chat_2")
    msg = SimpleNamespace()

    import src.core.language_detect as _ld

    orig_dl = _ld.detect_language
    # Симулируем ситуацию где auto_detect_direction вернёт что src==tgt (маловероятно, но safe)
    _ld.detect_language = lambda t: ""  # нет детекции → False

    try:
        handled = await bot._handle_auto_translate_message(msg, "Hello world!", "chat_2")
    finally:
        _ld.detect_language = orig_dl

    assert handled is False


@pytest.mark.asyncio
async def test_handle_auto_translate_message_silent_on_exception(tmp_path):
    """При исключении в translate_text — не падаем, возвращаем False."""
    from src.userbot.auto_translate import AutoTranslateMixin

    class TestBot(AutoTranslateMixin):
        def _auto_translate_state_path(self):
            return tmp_path / "auto_translate_chats.json"

    bot = TestBot()
    bot.add_auto_translate_chat("chat_err")
    msg = SimpleNamespace()

    import src.core.language_detect as _ld
    import src.core.translator_engine as _te

    orig_tt = _te.translate_text
    orig_dl = _ld.detect_language
    _te.translate_text = AsyncMock(side_effect=RuntimeError("LLM down"))
    _ld.detect_language = lambda t: "en"

    try:
        handled = await bot._handle_auto_translate_message(msg, "Hello world!", "chat_err")
    finally:
        _te.translate_text = orig_tt
        _ld.detect_language = orig_dl

    assert handled is False  # тихая деградация
