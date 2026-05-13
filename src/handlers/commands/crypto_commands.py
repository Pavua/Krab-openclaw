# -*- coding: utf-8 -*-
"""
crypto_commands — Phase 2 Wave 19 extraction (Session 28).

Команды security-домена:
  - !encrypt / !decrypt — симметричное шифрование (XOR + SHA-256 + Base64).
  - !qr — генерация QR-кода через segno.

Re-exported from ``command_handlers.py`` для обратной совместимости тестов
(`from src.handlers.command_handlers import handle_qr / handle_encrypt …`).
"""

from __future__ import annotations

import base64 as _base64
import hashlib as _hashlib
from typing import TYPE_CHECKING, Any

from pyrogram.types import Message

from ...core.exceptions import UserInputError
from ...core.logger import get_logger

if TYPE_CHECKING:
    from ...userbot_bridge import KraabUserbot

logger = get_logger(__name__)


def _ch_attr(name: str, default: Any) -> Any:
    """Lazy dual-namespace lookup: command_handlers сначала (для monkeypatch),
    fallback к локальному default."""
    try:
        from .. import command_handlers as _ch  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return default
    return getattr(_ch, name, default)


# ---------------------------------------------------------------------------
# !encrypt / !decrypt — симметричное шифрование
# ---------------------------------------------------------------------------


def _derive_key(password: str) -> bytes:
    """Выводит 32-байтный ключ из пароля через SHA-256."""
    return _hashlib.sha256(password.encode("utf-8")).digest()


def _xor_crypt(data: bytes, key: bytes) -> bytes:
    """XOR-шифрование/дешифрование с циклическим ключом."""
    key_len = len(key)
    return bytes(b ^ key[i % key_len] for i, b in enumerate(data))


def encrypt_text(password: str, text: str) -> str:
    """Шифрует текст паролем, возвращает Base64-строку."""
    key = _derive_key(password)
    ciphertext = _xor_crypt(text.encode("utf-8"), key)
    return _base64.b64encode(ciphertext).decode("ascii")


def decrypt_text(password: str, b64_cipher: str) -> str:
    """Дешифрует Base64-шифртекст паролем, возвращает исходный текст."""
    key = _derive_key(password)
    # мягкий паддинг
    stripped = b64_cipher.strip().replace("\n", "").replace(" ", "")
    padded = stripped + "=" * ((4 - len(stripped) % 4) % 4)
    ciphertext = _base64.b64decode(padded)
    return _xor_crypt(ciphertext, key).decode("utf-8")


async def handle_encrypt(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !encrypt — шифрование текста паролем.

    Формат: !encrypt <password> <текст>
    Возвращает зашифрованный Base64-блоб.
    """
    args = bot._get_command_args(message).strip()
    parts = args.split(" ", 1)
    if len(parts) < 2 or not parts[0] or not parts[1].strip():
        raise UserInputError(
            user_message=(
                "🔒 **Encrypt — справка**\n\n"
                "`!encrypt <пароль> <текст>` — зашифровать текст\n"
                "`!decrypt <пароль> <base64>` — расшифровать\n\n"
                "Алгоритм: XOR + SHA-256(пароль) + Base64"
            )
        )
    password, plaintext = parts[0], parts[1].strip()
    _encrypt = _ch_attr("encrypt_text", encrypt_text)
    result = _encrypt(password, plaintext)
    await message.reply(f"🔒 **Encrypted:**\n`{result}`")


async def handle_decrypt(bot: "KraabUserbot", message: Message) -> None:
    """
    Команда !decrypt — расшифровка текста паролем.

    Формат: !decrypt <password> <base64>
    Возвращает расшифрованный текст.
    """
    args = bot._get_command_args(message).strip()
    parts = args.split(" ", 1)
    if len(parts) < 2 or not parts[0] or not parts[1].strip():
        raise UserInputError(
            user_message=(
                "🔓 **Decrypt — справка**\n\n"
                "`!decrypt <пароль> <base64>` — расшифровать\n"
                "`!encrypt <пароль> <текст>` — зашифровать\n\n"
                "Алгоритм: XOR + SHA-256(пароль) + Base64"
            )
        )
    password, b64_cipher = parts[0], parts[1].strip()
    _decrypt = _ch_attr("decrypt_text", decrypt_text)
    try:
        result = _decrypt(password, b64_cipher)
    except Exception as exc:  # noqa: BLE001
        raise UserInputError(
            user_message=f"❌ Не удалось расшифровать: {exc}\n\nПроверь пароль и корректность Base64."
        ) from exc
    await message.reply(f"🔓 **Decrypted:**\n`{result}`")


# ---------------------------------------------------------------------------
# !qr — генерация QR-кода
# ---------------------------------------------------------------------------


async def handle_qr(bot: "KraabUserbot", message: Message) -> None:
    """Генерирует QR-код из текста/URL и отправляет фото."""
    import os
    import tempfile

    # Получаем текст: из аргументов или из reply-сообщения
    raw_args = bot._get_command_args(message).strip()

    if raw_args:
        text = raw_args
    elif message.reply_to_message:
        replied = message.reply_to_message
        # берём текст или подпись к медиа
        text = replied.text or replied.caption or ""
        text = text.strip()
    else:
        text = ""

    if not text:
        raise UserInputError(
            user_message="📷 Укажи текст или URL: `!qr <текст>`, либо ответь на сообщение."
        )

    # Генерируем QR через segno (чистый Python, без зависимостей от Pillow)
    try:
        import segno
    except ImportError:
        await message.reply("❌ Библиотека `segno` не установлена. Запусти: `pip install segno`")
        return

    # Создаём временный файл
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="krab_qr_")
    os.close(tmp_fd)

    # Wave 204: «Краб загружает фото...» на время генерации QR + аплоада.
    try:
        from ...userbot.typing_indicator import uploading_photo  # noqa: PLC0415

        _photo_indicator_cm: Any = uploading_photo(bot.client, message.chat.id)
    except Exception:  # noqa: BLE001
        from contextlib import nullcontext  # noqa: PLC0415

        _photo_indicator_cm = nullcontext()

    try:
        async with _photo_indicator_cm:
            qr = segno.make(text, error="m")
            # scale=10 → ~350px при версии 1; border=4 — стандартный quiet zone
            qr.save(tmp_path, kind="png", scale=10, border=4)

            caption = f"📷 QR: `{text[:80]}{'...' if len(text) > 80 else ''}`"
            await bot.client.send_photo(
                chat_id=message.chat.id,
                photo=tmp_path,
                caption=caption,
                reply_to_message_id=message.id,
            )
    finally:
        # Удаляем временный файл в любом случае
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
