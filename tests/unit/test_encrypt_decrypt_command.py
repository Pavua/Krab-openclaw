# -*- coding: utf-8 -*-
"""
Тесты для команд !encrypt / !decrypt.

Покрываем:
  - чистые функции: _derive_key, _xor_crypt, encrypt_text, decrypt_text
  - handle_encrypt: базовое шифрование, пустые аргументы, справка
  - handle_decrypt: базовое дешифрование, неверный пароль, невалидный base64, справка
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _derive_key,
    _xor_crypt,
    decrypt_text,
    encrypt_text,
    handle_decrypt,
    handle_encrypt,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Мок бота с _get_command_args."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message() -> AsyncMock:
    """Мок сообщения без reply."""
    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = 99999
    msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# Чистые функции
# ---------------------------------------------------------------------------


class TestDeriveKey:
    """Тесты функции _derive_key."""

    def test_returns_32_bytes(self):
        """SHA-256 возвращает ровно 32 байта."""
        key = _derive_key("any_password")
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_same_password_same_key(self):
        """Один пароль → один и тот же ключ (детерминированность)."""
        assert _derive_key("secret") == _derive_key("secret")

    def test_different_passwords_different_keys(self):
        """Разные пароли → разные ключи."""
        assert _derive_key("secret1") != _derive_key("secret2")

    def test_empty_password_works(self):
        """Пустой пароль тоже даёт 32 байта."""
        key = _derive_key("")
        assert len(key) == 32

    def test_unicode_password(self):
        """Юникод-пароль (русские символы) обрабатывается без ошибок."""
        key = _derive_key("пароль")
        assert len(key) == 32


class TestXorCrypt:
    """Тесты функции _xor_crypt."""

    def test_encrypt_decrypt_roundtrip(self):
        """XOR дважды = исходные данные."""
        key = b"key"
        data = b"hello world"
        assert _xor_crypt(_xor_crypt(data, key), key) == data

    def test_empty_data(self):
        """Пустые данные → пустой результат."""
        assert _xor_crypt(b"", b"key") == b""

    def test_key_is_cyclic(self):
        """Ключ используется циклически (данные длиннее ключа)."""
        key = b"\x01"
        data = b"\x01\x02\x03"
        result = _xor_crypt(data, key)
        assert result == bytes([0x00, 0x03, 0x02])

    def test_symmetry(self):
        """encrypt(encrypt(data)) == data при одном ключе."""
        key = b"any_key_bytes"
        data = b"test data 123"
        assert _xor_crypt(_xor_crypt(data, key), key) == data


class TestEncryptText:
    """Тесты функции encrypt_text."""

    def test_returns_string(self):
        """Результат — строка."""
        result = encrypt_text("pass", "hello")
        assert isinstance(result, str)

    def test_result_is_valid_base64(self):
        """Результат — валидный Base64."""
        import base64

        result = encrypt_text("pass", "hello")
        # Должен декодироваться без ошибок
        decoded = base64.b64decode(result)
        assert isinstance(decoded, bytes)

    def test_different_passwords_different_ciphertext(self):
        """Разные пароли → разные шифртексты."""
        c1 = encrypt_text("pass1", "hello")
        c2 = encrypt_text("pass2", "hello")
        assert c1 != c2

    def test_different_texts_different_ciphertext(self):
        """Разные тексты → разные шифртексты."""
        c1 = encrypt_text("pass", "hello")
        c2 = encrypt_text("pass", "world")
        assert c1 != c2

    def test_same_inputs_same_ciphertext(self):
        """Одинаковые пароль+текст → одинаковый шифртекст (детерминированность)."""
        assert encrypt_text("pass", "hello") == encrypt_text("pass", "hello")

    def test_russian_plaintext(self):
        """Русский текст шифруется без ошибок."""
        result = encrypt_text("пароль", "Секретное сообщение")
        assert result  # не пустой


class TestDecryptText:
    """Тесты функции decrypt_text."""

    def test_roundtrip_ascii(self):
        """encrypt → decrypt = исходный ASCII текст."""
        original = "hello world"
        assert decrypt_text("pass", encrypt_text("pass", original)) == original

    def test_roundtrip_russian(self):
        """encrypt → decrypt = исходный русский текст."""
        original = "Привет, мир!"
        assert decrypt_text("пароль", encrypt_text("пароль", original)) == original

    def test_roundtrip_unicode_symbols(self):
        """Спецсимволы и эмодзи проходят roundtrip."""
        original = "Test! @#$% 🐚 Краб"
        assert decrypt_text("key", encrypt_text("key", original)) == original

    def test_wrong_password_gives_garbage(self):
        """Неверный пароль не вызывает исключение, но даёт неправильный текст."""
        cipher = encrypt_text("correct", "secret")
        # Не упасть — но дать мусор или UnicodeDecodeError (это нормально)
        try:
            result = decrypt_text("wrong", cipher)
            # Если декодирование прошло — результат не должен совпадать с оригиналом
            assert result != "secret"
        except (UnicodeDecodeError, ValueError):
            pass  # Ожидаемо при неверном пароле

    def test_empty_string_roundtrip(self):
        """Пустая строка шифруется и дешифруется."""
        assert decrypt_text("pass", encrypt_text("pass", "")) == ""

    def test_multiline_roundtrip(self):
        """Многострочный текст проходит roundtrip."""
        original = "строка 1\nстрока 2\nстрока 3"
        assert decrypt_text("pass", encrypt_text("pass", original)) == original


# ---------------------------------------------------------------------------
# handle_encrypt
# ---------------------------------------------------------------------------


class TestHandleEncrypt:
    """Тесты хендлера !encrypt."""

    @pytest.mark.asyncio
    async def test_basic_encrypt(self):
        """!encrypt mypass hello → ответ содержит зашифрованный Base64."""
        bot = _make_bot("mypass hello")
        msg = _make_message()
        await handle_encrypt(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "🔒" in reply_text
        # Результат должен дешифроваться обратно
        import re

        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert decrypt_text("mypass", match.group(1)) == "hello"

    @pytest.mark.asyncio
    async def test_encrypt_russian_text(self):
        """!encrypt pass Привет мир → дешифруется обратно."""
        import re

        bot = _make_bot("pass Привет мир")
        msg = _make_message()
        await handle_encrypt(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert decrypt_text("pass", match.group(1)) == "Привет мир"

    @pytest.mark.asyncio
    async def test_encrypt_no_args_raises(self):
        """!encrypt без аргументов → UserInputError со справкой."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_encrypt(bot, msg)
        err = exc_info.value.user_message
        assert "encrypt" in err.lower() or "пароль" in err.lower()

    @pytest.mark.asyncio
    async def test_encrypt_only_password_raises(self):
        """!encrypt mypass (без текста) → UserInputError."""
        bot = _make_bot("mypass")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_encrypt(bot, msg)

    @pytest.mark.asyncio
    async def test_encrypt_password_with_spaces_in_text(self):
        """Текст может содержать пробелы — они сохраняются."""
        import re

        bot = _make_bot("p text with spaces here")
        msg = _make_message()
        await handle_encrypt(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        match = re.search(r"`([A-Za-z0-9+/=]+)`", reply_text)
        assert match
        assert decrypt_text("p", match.group(1)) == "text with spaces here"

    @pytest.mark.asyncio
    async def test_encrypt_shows_encrypted_label(self):
        """Ответ содержит метку Encrypted."""
        bot = _make_bot("pass secret")
        msg = _make_message()
        await handle_encrypt(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "Encrypted" in reply_text or "encrypt" in reply_text.lower()


# ---------------------------------------------------------------------------
# handle_decrypt
# ---------------------------------------------------------------------------


class TestHandleDecrypt:
    """Тесты хендлера !decrypt."""

    @pytest.mark.asyncio
    async def test_basic_decrypt(self):
        """!decrypt pass <cipher> → исходный текст."""
        cipher = encrypt_text("pass", "hello")
        bot = _make_bot(f"pass {cipher}")
        msg = _make_message()
        await handle_decrypt(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "hello" in reply_text

    @pytest.mark.asyncio
    async def test_decrypt_russian(self):
        """!decrypt pass <cipher> → русский текст восстанавливается."""
        cipher = encrypt_text("пароль", "Секрет")
        bot = _make_bot(f"пароль {cipher}")
        msg = _make_message()
        await handle_decrypt(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "Секрет" in reply_text

    @pytest.mark.asyncio
    async def test_decrypt_no_args_raises(self):
        """!decrypt без аргументов → UserInputError со справкой."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_decrypt(bot, msg)
        err = exc_info.value.user_message
        assert "decrypt" in err.lower() or "пароль" in err.lower()

    @pytest.mark.asyncio
    async def test_decrypt_only_password_raises(self):
        """!decrypt mypass (без шифртекста) → UserInputError."""
        bot = _make_bot("mypass")
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_decrypt(bot, msg)

    @pytest.mark.asyncio
    async def test_decrypt_invalid_base64_raises(self):
        """!decrypt pass <невалидный base64> → UserInputError."""
        bot = _make_bot("pass ЭТО_НЕ_BASE64!!!")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_decrypt(bot, msg)
        assert (
            "расшифровать" in exc_info.value.user_message.lower()
            or "decrypt" in exc_info.value.user_message.lower()
        )

    @pytest.mark.asyncio
    async def test_decrypt_shows_decrypted_label(self):
        """Ответ содержит метку Decrypted."""
        cipher = encrypt_text("key", "text")
        bot = _make_bot(f"key {cipher}")
        msg = _make_message()
        await handle_decrypt(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "Decrypted" in reply_text or "decrypt" in reply_text.lower() or "🔓" in reply_text

    @pytest.mark.asyncio
    async def test_encrypt_decrypt_full_roundtrip_via_handlers(self):
        """encrypt-handler → decrypt-handler → исходный текст."""
        import re

        original = "roundtrip test 123"
        password = "testkey"

        # Шифруем через хендлер
        enc_bot = _make_bot(f"{password} {original}")
        enc_msg = _make_message()
        await handle_encrypt(enc_bot, enc_msg)
        enc_reply = enc_msg.reply.call_args[0][0]
        match = re.search(r"`([A-Za-z0-9+/=]+)`", enc_reply)
        assert match
        cipher = match.group(1)

        # Дешифруем через хендлер
        dec_bot = _make_bot(f"{password} {cipher}")
        dec_msg = _make_message()
        await handle_decrypt(dec_bot, dec_msg)
        dec_reply = dec_msg.reply.call_args[0][0]
        assert original in dec_reply

    @pytest.mark.asyncio
    async def test_decrypt_with_valid_base64_wrong_password_raises_or_garbage(self):
        """Валидный Base64 с неверным паролем: либо ошибка декодирования, либо мусор."""
        cipher = encrypt_text("correct_pass", "secret message")
        bot = _make_bot(f"wrong_pass {cipher}")
        msg = _make_message()
        try:
            await handle_decrypt(bot, msg)
            # Если не упало — текст в ответе не должен содержать оригинал
            reply_text = msg.reply.call_args[0][0]
            assert "secret message" not in reply_text
        except UserInputError:
            pass  # Ожидаемо — UnicodeDecodeError → UserInputError
