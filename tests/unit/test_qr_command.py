# -*- coding: utf-8 -*-
"""
Тесты команды !qr — генерация QR-кода.

Покрываем:
- handle_qr: текст из аргументов
- handle_qr: текст из reply-сообщения
- handle_qr: подпись к медиа из reply
- handle_qr: пустой ввод → UserInputError
- handle_qr: очень длинный текст (усечение caption)
- handle_qr: временный файл удаляется после отправки
- handle_qr: временный файл удаляется даже при ошибке send_photo
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_qr


# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    bot.client = MagicMock()
    bot.client.send_photo = AsyncMock()
    return bot


def _make_message(
    reply_text: str | None = None,
    reply_caption: str | None = None,
    chat_id: int = 42,
    message_id: int = 1,
) -> MagicMock:
    msg = MagicMock()
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.id = message_id
    msg.reply = AsyncMock()

    if reply_text is not None or reply_caption is not None:
        replied = MagicMock()
        replied.text = reply_text or ""
        replied.caption = reply_caption or ""
        msg.reply_to_message = replied
    else:
        msg.reply_to_message = None

    return msg


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestHandleQr:
    """Тесты !qr."""

    @pytest.mark.asyncio
    async def test_qr_из_аргументов(self, tmp_path) -> None:
        """Генерирует QR из текста, переданного аргументом."""
        bot = _make_bot(command_args="https://example.com")
        msg = _make_message()

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close") as mock_close, \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink") as mock_unlink, \
             patch("segno.make") as mock_segno_make:

            tmp_file = str(tmp_path / "krab_qr_test.png")
            # Создаём пустой файл чтобы segno.save не падал
            with open(tmp_file, "w") as f:
                f.write("")
            mock_mkstemp.return_value = (999, tmp_file)

            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        mock_segno_make.assert_called_once_with("https://example.com", error="m")
        mock_qr.save.assert_called_once_with(tmp_file, kind="png", scale=10, border=4)
        bot.client.send_photo.assert_awaited_once()
        call_kwargs = bot.client.send_photo.call_args
        assert call_kwargs.kwargs["chat_id"] == 42
        assert "https://example.com" in call_kwargs.kwargs["caption"]

    @pytest.mark.asyncio
    async def test_qr_из_reply_текст(self, tmp_path) -> None:
        """Генерирует QR из текста reply-сообщения."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="Привет мир")

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink"), \
             patch("segno.make") as mock_segno_make:

            tmp_file = str(tmp_path / "krab_qr_reply.png")
            mock_mkstemp.return_value = (998, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        mock_segno_make.assert_called_once_with("Привет мир", error="m")
        bot.client.send_photo.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_qr_из_reply_подпись(self, tmp_path) -> None:
        """Генерирует QR из caption медиа в reply-сообщении."""
        bot = _make_bot(command_args="")
        # text пустой, caption есть
        msg = _make_message(reply_text="", reply_caption="Ссылка на фото")

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink"), \
             patch("segno.make") as mock_segno_make:

            tmp_file = str(tmp_path / "krab_qr_cap.png")
            mock_mkstemp.return_value = (997, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        mock_segno_make.assert_called_once_with("Ссылка на фото", error="m")

    @pytest.mark.asyncio
    async def test_qr_пустой_ввод_вызывает_ошибку(self) -> None:
        """Если нет ни аргументов, ни reply — UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message()  # reply_to_message = None

        with pytest.raises(UserInputError):
            await handle_qr(bot, msg)

    @pytest.mark.asyncio
    async def test_qr_пустой_reply_вызывает_ошибку(self) -> None:
        """Reply без текста и без caption → UserInputError."""
        bot = _make_bot(command_args="")
        msg = _make_message(reply_text="", reply_caption="")

        with pytest.raises(UserInputError):
            await handle_qr(bot, msg)

    @pytest.mark.asyncio
    async def test_qr_длинный_текст_усечение_caption(self, tmp_path) -> None:
        """Caption усекается до 80 символов с '...'."""
        long_text = "A" * 120
        bot = _make_bot(command_args=long_text)
        msg = _make_message()

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink"), \
             patch("segno.make") as mock_segno_make:

            tmp_file = str(tmp_path / "krab_qr_long.png")
            mock_mkstemp.return_value = (996, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        call_kwargs = bot.client.send_photo.call_args.kwargs
        assert "..." in call_kwargs["caption"]
        assert len(call_kwargs["caption"]) < len(long_text) + 20  # усечено

    @pytest.mark.asyncio
    async def test_qr_файл_удаляется_после_отправки(self, tmp_path) -> None:
        """Временный файл удаляется даже после успешной отправки."""
        bot = _make_bot(command_args="test")
        msg = _make_message()

        tmp_file = str(tmp_path / "krab_qr_del.png")
        # Создаём реальный файл — проверяем что он будет удалён
        with open(tmp_file, "w") as f:
            f.write("x")

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("segno.make") as mock_segno_make:

            mock_mkstemp.return_value = (995, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        # Файл должен быть удалён
        assert not os.path.exists(tmp_file)

    @pytest.mark.asyncio
    async def test_qr_файл_удаляется_при_ошибке_send(self, tmp_path) -> None:
        """Временный файл удаляется даже если send_photo бросает исключение."""
        bot = _make_bot(command_args="test")
        bot.client.send_photo = AsyncMock(side_effect=RuntimeError("network error"))
        msg = _make_message()

        tmp_file = str(tmp_path / "krab_qr_err.png")
        with open(tmp_file, "w") as f:
            f.write("x")

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("segno.make") as mock_segno_make:

            mock_mkstemp.return_value = (994, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            with pytest.raises(RuntimeError, match="network error"):
                await handle_qr(bot, msg)

        # Файл должен быть удалён несмотря на ошибку
        assert not os.path.exists(tmp_file)

    @pytest.mark.asyncio
    async def test_qr_аргумент_приоритетнее_reply(self, tmp_path) -> None:
        """Если есть аргументы И reply — используются аргументы."""
        bot = _make_bot(command_args="args_text")
        msg = _make_message(reply_text="reply_text")

        with patch("tempfile.mkstemp") as mock_mkstemp, \
             patch("os.close"), \
             patch("os.path.exists", return_value=False), \
             patch("os.unlink"), \
             patch("segno.make") as mock_segno_make:

            tmp_file = str(tmp_path / "krab_qr_prio.png")
            mock_mkstemp.return_value = (993, tmp_file)
            mock_qr = MagicMock()
            mock_segno_make.return_value = mock_qr

            await handle_qr(bot, msg)

        # QR должен быть сгенерирован из аргумента, не из reply
        mock_segno_make.assert_called_once_with("args_text", error="m")
