# -*- coding: utf-8 -*-
"""
Tests Phase 2 Wave 19: src/handlers/commands/crypto_commands.py.

Покрываем:
  - Прямой импорт из commands.crypto_commands работает.
  - Re-export из command_handlers сохраняет identity.
  - encrypt_text/decrypt_text round-trip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers import command_handlers as ch
from src.handlers.commands import crypto_commands as cc


def test_module_re_exports_match_command_handlers():
    """command_handlers re-exports должны указывать на новый модуль."""
    assert ch.handle_qr is cc.handle_qr
    assert ch.handle_encrypt is cc.handle_encrypt
    assert ch.handle_decrypt is cc.handle_decrypt
    assert ch.encrypt_text is cc.encrypt_text
    assert ch.decrypt_text is cc.decrypt_text
    assert ch._derive_key is cc._derive_key
    assert ch._xor_crypt is cc._xor_crypt


def test_encrypt_decrypt_round_trip():
    """encrypt_text + decrypt_text должны быть взаимно обратными."""
    plaintext = "Привет, Краб! 🦀 Hello world."
    password = "s3cr3t-p@ss"
    cipher = cc.encrypt_text(password, plaintext)
    assert cipher  # неэмпти base64
    assert cipher != plaintext
    recovered = cc.decrypt_text(password, cipher)
    assert recovered == plaintext


def test_decrypt_with_wrong_password_garbles():
    """Неверный пароль должен дать мусор или UnicodeDecodeError."""
    cipher = cc.encrypt_text("right", "secret message")
    try:
        result = cc.decrypt_text("wrong", cipher)
        # Если декодировалось — должно отличаться
        assert result != "secret message"
    except UnicodeDecodeError:
        # Тоже валидный исход: мусор-байты не валидный UTF-8
        pass


@pytest.mark.asyncio
async def test_handle_encrypt_no_args_raises():
    """!encrypt без аргументов → UserInputError со справкой."""
    bot = MagicMock()
    bot._get_command_args.return_value = ""
    msg = MagicMock()
    msg.reply = AsyncMock()
    with pytest.raises(UserInputError) as exc:
        await cc.handle_encrypt(bot, msg)
    assert "Encrypt" in exc.value.user_message


@pytest.mark.asyncio
async def test_handle_decrypt_invalid_base64_raises():
    """!decrypt с невалидным base64 → UserInputError."""
    bot = MagicMock()
    bot._get_command_args.return_value = "pass !!!notbase64!!!"
    msg = MagicMock()
    msg.reply = AsyncMock()
    with pytest.raises(UserInputError) as exc:
        await cc.handle_decrypt(bot, msg)
    assert "расшифровать" in exc.value.user_message.lower()


@pytest.mark.asyncio
async def test_handle_qr_empty_args_raises():
    """!qr без аргументов и без reply → UserInputError."""
    bot = MagicMock()
    bot._get_command_args.return_value = ""
    msg = MagicMock()
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    with pytest.raises(UserInputError):
        await cc.handle_qr(bot, msg)
