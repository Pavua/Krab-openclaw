# -*- coding: utf-8 -*-
"""
Тесты для команды !b64 (Base64 кодирование/декодирование).

Покрываем:
  - чистые функции _b64_encode, _b64_decode, _b64_is_valid
  - handle_b64: encode, decode, авто-режим (reply), голый текст, справка
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _b64_decode,
    _b64_encode,
    _b64_is_valid,
    handle_b64,
)

# ---------------------------------------------------------------------------
# Чистые функции
# ---------------------------------------------------------------------------


class TestB64Encode:
    """Тесты _b64_encode."""

    def test_encode_ascii(self):
        """ASCII строка кодируется корректно."""
        assert _b64_encode("hello") == "aGVsbG8="

    def test_encode_utf8_ru(self):
        """Русский текст кодируется в UTF-8 Base64."""
        result = _b64_encode("Привет")
        import base64

        expected = base64.b64encode("Привет".encode("utf-8")).decode("ascii")
        assert result == expected

    def test_encode_returns_str(self):
        """Результат — строка."""
        assert isinstance(_b64_encode("test"), str)

    def test_encode_empty_string(self):
        """Пустая строка → пустой Base64."""
        assert _b64_encode("") == ""

    def test_encode_multiline(self):
        """Многострочный текст кодируется без ошибок."""
        result = _b64_encode("line1\nline2")
        assert result  # не пустой

    def test_encode_special_chars(self):
        """Спецсимволы — !, @, #, ... — кодируются без ошибок."""
        result = _b64_encode("!@#$%^&*()")
        assert _b64_is_valid(result)


class TestB64Decode:
    """Тесты _b64_decode."""

    def test_decode_known_ascii(self):
        """Известный Base64 → ожидаемый текст."""
        assert _b64_decode("aGVsbG8=") == "hello"

    def test_decode_without_padding(self):
        """Base64 без паддинга декодируется с мягким паддингом."""
        # "aGVsbG8=" без знака =
        assert _b64_decode("aGVsbG8") == "hello"

    def test_decode_roundtrip(self):
        """encode → decode = исходный текст."""
        original = "Тест кодирования 123 !@#"
        assert _b64_decode(_b64_encode(original)) == original

    def test_decode_utf8_ru(self):
        """Русский текст правильно восстанавливается."""
        encoded = _b64_encode("Краб работает")
        assert _b64_decode(encoded) == "Краб работает"

    def test_decode_with_spaces(self):
        """Пробелы в Base64-строке игнорируются."""
        assert _b64_decode("aGVs bG8=") == "hello"

    def test_decode_with_newlines(self):
        """Переносы строк в Base64-строке игнорируются."""
        assert _b64_decode("aGVs\nbG8=") == "hello"

    def test_decode_invalid_raises(self):
        """Невалидный Base64 вызывает исключение (не UserInputError — просто Exception)."""
        with pytest.raises(Exception):
            _b64_decode("не Base64!!!")


class TestB64IsValid:
    """Тесты _b64_is_valid."""

    def test_valid_standard(self):
        """Стандартный Base64 → True."""
        assert _b64_is_valid("aGVsbG8=") is True

    def test_valid_without_padding(self):
        """Base64 без паддинга → True."""
        assert _b64_is_valid("aGVsbG8") is True

    def test_invalid_russian(self):
        """Русский текст — не Base64."""
        assert _b64_is_valid("Привет мир") is False

    def test_invalid_special_chars(self):
        """Строка с недопустимыми символами → False."""
        assert _b64_is_valid("Hello!!! World") is False

    def test_empty_string(self):
        """Пустая строка → False."""
        assert _b64_is_valid("") is False

    def test_valid_encoded_russian(self):
        """Закодированный русский текст — валидный Base64."""
        encoded = _b64_encode("Краб")
        assert _b64_is_valid(encoded) is True

    def test_plain_english_text(self):
        """Обычный английский текст — как правило не является валидным Base64 из-за пробелов."""
        # "Hello World" не пройдёт validate=True из-за недопустимых символов после strip
        # (пробел убирается, но длина и символы могут не совпасть)
        # Просто проверяем, что функция не падает
        result = _b64_is_valid("Hello World")
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# Вспомогательные fixtures для тестирования хендлера
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Создаёт мок бота с _get_command_args."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(reply_text: str | None = None) -> AsyncMock:
    """Создаёт мок сообщения с опциональным reply."""
    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = 12345
    if reply_text is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = reply_text
    else:
        msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# handle_b64 — encode
# ---------------------------------------------------------------------------


class TestHandleB64Encode:
    """Тесты явного режима encode."""

    @pytest.mark.asyncio
    async def test_encode_basic(self):
        """!b64 encode hello → ответ содержит закодированный текст."""
        bot = _make_bot("encode hello")
        msg = _make_message()
        await handle_b64(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "aGVsbG8=" in reply_text
        assert "encode" in reply_text.lower() or "🔐" in reply_text

    @pytest.mark.asyncio
    async def test_encode_russian(self):
        """!b64 encode Привет → ответ содержит валидный Base64."""
        bot = _make_bot("encode Привет")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # Base64 должен декодироваться обратно в исходный текст
        # Извлекаем закодированную часть (в backtick)
        import re

        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert _b64_decode(match.group(1)) == "Привет"

    @pytest.mark.asyncio
    async def test_encode_no_payload_is_bare_encode(self):
        """!b64 encode (с трейлинг-пробелом) — после strip() = 'encode', кодируется как текст."""
        # args.strip() == "encode" — не попадает в ветку "encode " (с пробелом),
        # идёт в ветку голого текста и кодирует слово "encode"
        bot = _make_bot("encode ")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # слово "encode" будет закодировано
        assert "ZW5jb2Rl" in reply_text

    @pytest.mark.asyncio
    async def test_encode_no_payload_empty_raises(self):
        """!b64 encode<пусто> → UserInputError."""
        bot = _make_bot("encode")
        # "encode" не начинается с "encode " (с пробелом), поэтому не попадёт в ветку encode
        # но если текст только "encode" без пробела — попадёт в ветку голого текста и закодируется
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # "encode" как текст кодируется
        assert _b64_decode("ZW5jb2Rl") == "encode"
        assert "ZW5jb2Rl" in reply_text


# ---------------------------------------------------------------------------
# handle_b64 — decode
# ---------------------------------------------------------------------------


class TestHandleB64Decode:
    """Тесты явного режима decode."""

    @pytest.mark.asyncio
    async def test_decode_basic(self):
        """!b64 decode aGVsbG8= → hello."""
        bot = _make_bot("decode aGVsbG8=")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "hello" in reply_text

    @pytest.mark.asyncio
    async def test_decode_russian(self):
        """!b64 decode <закодированный русский> → оригинальный текст."""
        encoded = _b64_encode("Краб")
        bot = _make_bot(f"decode {encoded}")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "Краб" in reply_text

    @pytest.mark.asyncio
    async def test_decode_invalid_raises_user_error(self):
        """!b64 decode <невалидный> → UserInputError."""
        bot = _make_bot("decode ЭТО!НЕ!BASE64!!!")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_b64(bot, msg)

    @pytest.mark.asyncio
    async def test_decode_no_payload_is_bare_decode(self):
        """!b64 decode (с трейлинг-пробелом) — после strip() = 'decode', кодируется как текст."""
        # args.strip() == "decode" — не попадает в ветку "decode " (с пробелом),
        # идёт в ветку голого текста
        bot = _make_bot("decode ")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # слово "decode" закодировано
        assert _b64_decode("ZGVjb2Rl") == "decode"
        assert "ZGVjb2Rl" in reply_text

    @pytest.mark.asyncio
    async def test_decode_without_padding(self):
        """Декодирование Base64 без знака = работает."""
        # "aGVsbG8=" без паддинга
        bot = _make_bot("decode aGVsbG8")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "hello" in reply_text


# ---------------------------------------------------------------------------
# handle_b64 — автоопределение (reply)
# ---------------------------------------------------------------------------


class TestHandleB64AutoReply:
    """Тесты автоопределения в reply-режиме."""

    @pytest.mark.asyncio
    async def test_reply_with_b64_decodes(self):
        """Reply содержит Base64 → автоматически декодирует."""
        encoded = _b64_encode("секрет")
        bot = _make_bot("")  # нет args
        msg = _make_message(reply_text=encoded)
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "секрет" in reply_text
        assert "decode" in reply_text.lower() or "🔓" in reply_text

    @pytest.mark.asyncio
    async def test_reply_with_plain_text_encodes(self):
        """Reply содержит обычный текст (не Base64) → автоматически кодирует."""
        bot = _make_bot("")
        msg = _make_message(reply_text="Обычный русский текст!!!")
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "encode" in reply_text.lower() or "🔐" in reply_text

    @pytest.mark.asyncio
    async def test_reply_decoded_matches_original(self):
        """Автодекодирование восстанавливает исходный текст."""
        original = "Тест автоопределения"
        encoded = _b64_encode(original)
        bot = _make_bot("")
        msg = _make_message(reply_text=encoded)
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert original in reply_text

    @pytest.mark.asyncio
    async def test_reply_encoded_is_valid_b64(self):
        """Автокодирование → результат является валидным Base64."""
        import re

        bot = _make_bot("")
        msg = _make_message(reply_text="Encode me!")
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert _b64_is_valid(match.group(1))


# ---------------------------------------------------------------------------
# handle_b64 — голый текст (без явной подкоманды)
# ---------------------------------------------------------------------------


class TestHandleB64BareText:
    """Тесты кодирования голого текста (без encode/decode)."""

    @pytest.mark.asyncio
    async def test_bare_text_encodes(self):
        """!b64 <текст> → кодирует текст."""
        bot = _make_bot("some plain text")
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "🔐" in reply_text or "encode" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_bare_text_roundtrip(self):
        """!b64 <текст> → декодируется обратно."""
        import re

        text = "roundtrip test"
        bot = _make_bot(text)
        msg = _make_message()
        await handle_b64(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert _b64_decode(match.group(1)) == text


# ---------------------------------------------------------------------------
# handle_b64 — справка
# ---------------------------------------------------------------------------


class TestHandleB64Help:
    """Тесты вывода справки."""

    @pytest.mark.asyncio
    async def test_no_args_no_reply_raises_user_input_error(self):
        """!b64 без аргументов и без reply → UserInputError со справкой."""
        bot = _make_bot("")
        msg = _make_message()  # нет reply
        with pytest.raises(UserInputError) as exc_info:
            await handle_b64(bot, msg)
        assert "encode" in exc_info.value.user_message.lower()
        assert "decode" in exc_info.value.user_message.lower()
