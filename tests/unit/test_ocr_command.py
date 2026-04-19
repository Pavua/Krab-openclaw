# -*- coding: utf-8 -*-
"""
Юнит-тесты для !ocr command handler.

Покрываем:
  - handle_ocr без reply → UserInputError
  - handle_ocr reply на не-фото → UserInputError
  - handle_ocr reply на фото → скачивает, base64, отправляет в vision
  - handle_ocr reply на документ-изображение → работает
  - !ocr без подсказки → дефолтный OCR-промпт
  - !ocr <подсказка> → подсказка встраивается в промпт
  - session_id = ocr_{chat_id}
  - force_cloud=True обязательно
  - disable_tools=True
  - пустой ответ AI → сообщение об ошибке
  - streaming-чанки склеиваются
  - исключение в openclaw → graceful error
  - пустой download → сообщение об ошибке
  - handle_ocr экспортируется из handlers
  - !ocr в реестре команд
  - ответ форматируется с заголовком "OCR:"
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_ocr

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_photo_replied(with_photo: bool = True, with_doc_image: bool = False) -> SimpleNamespace:
    """Создаёт stub сообщения с фото (или без)."""
    if with_photo:
        photo = SimpleNamespace(file_id="photo_file_id")
        document = None
    elif with_doc_image:
        photo = None
        document = SimpleNamespace(mime_type="image/jpeg", file_id="doc_file_id")
    else:
        photo = None
        document = SimpleNamespace(mime_type="application/pdf", file_id="doc_file_id")

    img_bytes = b"\xff\xd8\xff\xe0test_jpeg_content"

    async def fake_download(in_memory=None):
        if in_memory is not None:
            in_memory.write(img_bytes)
        return in_memory

    return SimpleNamespace(
        photo=photo,
        document=document,
        download=fake_download,
    )


def _make_message(
    command_args: str = "",
    chat_id: int = 55555,
    replied: SimpleNamespace | None = None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    edit_mock = AsyncMock()
    status_msg = SimpleNamespace(edit=edit_mock)

    msg = SimpleNamespace(
        text=f"!ocr {command_args}".strip(),
        reply=AsyncMock(return_value=status_msg),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=replied,
    )

    bot = SimpleNamespace(_get_command_args=lambda _m: command_args)
    return bot, msg


# ===========================================================================
# Ошибочные входные данные
# ===========================================================================


class TestHandleOcrValidation:
    """Валидация входных данных."""

    @pytest.mark.asyncio
    async def test_нет_reply_поднимает_userinputerror(self) -> None:
        """!ocr без reply → UserInputError."""
        bot, msg = _make_message(command_args="", replied=None)
        with pytest.raises(UserInputError):
            await handle_ocr(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_на_текст_поднимает_userinputerror(self) -> None:
        """!ocr в reply на текстовое сообщение → UserInputError."""
        replied = SimpleNamespace(photo=None, document=None)
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError):
            await handle_ocr(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_на_не_image_документ_поднимает_userinputerror(self) -> None:
        """!ocr в reply на PDF-документ → UserInputError."""
        replied = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(mime_type="application/pdf", file_id="pdf_id"),
        )
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError):
            await handle_ocr(bot, msg)

    @pytest.mark.asyncio
    async def test_userinputerror_без_reply_содержит_hint(self) -> None:
        """Сообщение ошибки содержит подсказку про !ocr."""
        bot, msg = _make_message(replied=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_ocr(bot, msg)
        assert "ocr" in exc_info.value.user_message.lower() or "фото" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_reply_на_фото_без_document_ok(self) -> None:
        """Фото без документа принимается нормально."""
        replied = _make_photo_replied(with_photo=True)
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, images=None, **_kw):
            yield "Текст с фото"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            # Не должно падать
            await handle_ocr(bot, msg)


# ===========================================================================
# Скачивание и base64
# ===========================================================================


class TestHandleOcrDownload:
    """Скачивание и кодирование изображения."""

    @pytest.mark.asyncio
    async def test_скачивает_фото_и_передаёт_base64_в_openclaw(self) -> None:
        """Байты фото → base64 → images=[b64] в send_message_stream."""
        img_bytes = b"\xff\xd8\xff\xe0some_jpeg"
        expected_b64 = base64.b64encode(img_bytes).decode("ascii")

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="test"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        captured_images: list[list[str]] = []

        async def fake_stream(message, chat_id, images=None, **_kw):
            captured_images.append(images or [])
            yield "Текст: Hello World"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert len(captured_images) == 1
        assert len(captured_images[0]) == 1
        assert captured_images[0][0] == expected_b64

    @pytest.mark.asyncio
    async def test_пустой_download_возвращает_ошибку(self) -> None:
        """Если download вернул 0 байт → edit с ошибкой."""

        async def empty_download(in_memory=None):
            # ничего не пишем
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="test"),
            document=None,
            download=empty_download,
        )
        bot, msg = _make_message(replied=replied)

        await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_документ_изображение_принимается(self) -> None:
        """!ocr reply на документ image/jpeg → тоже обрабатывается."""
        img_bytes = b"\xff\xd8data"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(mime_type="image/jpeg", file_id="doc_id"),
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, images=None, **_kw):
            yield "Invoice #1234"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_called_once()
        assert "Invoice" in status_msg.edit.call_args[0][0]

    @pytest.mark.asyncio
    async def test_png_документ_принимается(self) -> None:
        """!ocr reply на документ image/png → работает."""
        img_bytes = b"\x89PNG\r\ndata"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(mime_type="image/png", file_id="png_id"),
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, images=None, **_kw):
            yield "PNG text"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_called_once()


# ===========================================================================
# OCR-промпт
# ===========================================================================


class TestHandleOcrPrompt:
    """Формирование OCR-промпта."""

    @pytest.mark.asyncio
    async def test_без_подсказки_дефолтный_ocr_промпт(self) -> None:
        """!ocr без аргументов → промпт содержит 'извлеки' или 'текст'."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(command_args="", replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, images=None, **_kw):
            captured.append(message)
            yield "Текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert len(captured) == 1
        prompt = captured[0].lower()
        assert "извлеки" in prompt or "текст" in prompt or "ocr" in prompt

    @pytest.mark.asyncio
    async def test_с_подсказкой_подсказка_встраивается_в_промпт(self) -> None:
        """!ocr чек из магазина → подсказка в промпте."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(command_args="чек из магазина", replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, images=None, **_kw):
            captured.append(message)
            yield "Итого: 15.99"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert "чек из магазина" in captured[0]

    @pytest.mark.asyncio
    async def test_с_подсказкой_не_содержит_только_дефолтный_промпт(self) -> None:
        """Когда есть подсказка, промпт изменён относительно дефолтного."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot_no_hint, msg_no_hint = _make_message(command_args="", replied=replied)
        bot_hint, msg_hint = _make_message(command_args="номер счёта", replied=replied)

        prompts_no_hint: list[str] = []
        prompts_hint: list[str] = []

        async def fake_stream_nh(message, chat_id, **_kw):
            prompts_no_hint.append(message)
            yield "x"

        async def fake_stream_h(message, chat_id, **_kw):
            prompts_hint.append(message)
            yield "x"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream_nh,
        ):
            await handle_ocr(bot_no_hint, msg_no_hint)

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream_h,
        ):
            await handle_ocr(bot_hint, msg_hint)

        # Промпты с подсказкой и без должны отличаться
        assert prompts_no_hint[0] != prompts_hint[0]

    @pytest.mark.asyncio
    async def test_промпт_требует_дословного_извлечения(self) -> None:
        """Дефолтный промпт содержит указание сохранить оригинальный текст."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(command_args="", replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        prompt = captured[0].lower()
        # Промпт про дословное извлечение
        assert "дословно" in prompt or "сохрани" in prompt or "текст" in prompt


# ===========================================================================
# Параметры вызова OpenClaw
# ===========================================================================


class TestHandleOcrOpenClawParams:
    """Параметры, передаваемые в send_message_stream."""

    @pytest.mark.asyncio
    async def test_force_cloud_true(self) -> None:
        """!ocr всегда вызывает send_message_stream с force_cloud=True."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        captured: list[dict] = []

        async def fake_stream(message, chat_id, force_cloud=False, **_kw):
            captured.append({"force_cloud": force_cloud})
            yield "текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert captured[0]["force_cloud"] is True

    @pytest.mark.asyncio
    async def test_disable_tools_true(self) -> None:
        """!ocr вызывает send_message_stream с disable_tools=True."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        captured: list[dict] = []

        async def fake_stream(message, chat_id, disable_tools=False, **_kw):
            captured.append({"disable_tools": disable_tools})
            yield "текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert captured[0]["disable_tools"] is True

    @pytest.mark.asyncio
    async def test_session_id_изолирован_по_chat_id(self) -> None:
        """chat_id передаётся как 'ocr_{chat_id}'."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(chat_id=99887, replied=replied)

        captured_sessions: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured_sessions.append(chat_id)
            yield "текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert captured_sessions[0] == "ocr_99887"

    @pytest.mark.asyncio
    async def test_разные_чаты_разные_сессии(self) -> None:
        """Два разных chat_id → разные session_id."""
        img_bytes = b"\xff\xd8\xff"

        sessions: list[str] = []

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        async def fake_stream(message, chat_id, **_kw):
            sessions.append(chat_id)
            yield "текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            replied1 = SimpleNamespace(
                photo=SimpleNamespace(file_id="x"), document=None, download=fake_download
            )
            bot1, msg1 = _make_message(chat_id=111, replied=replied1)
            await handle_ocr(bot1, msg1)

            replied2 = SimpleNamespace(
                photo=SimpleNamespace(file_id="y"), document=None, download=fake_download
            )
            bot2, msg2 = _make_message(chat_id=222, replied=replied2)
            await handle_ocr(bot2, msg2)

        assert sessions[0] == "ocr_111"
        assert sessions[1] == "ocr_222"
        assert sessions[0] != sessions[1]

    @pytest.mark.asyncio
    async def test_session_id_не_совпадает_с_img_session(self) -> None:
        """OCR-сессия отличается от img-сессии для того же чата."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(chat_id=12345, replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(chat_id)
            yield "текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert captured[0] == "ocr_12345"
        assert captured[0] != "img_12345"


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleOcrResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_содержит_заголовок_ocr(self) -> None:
        """Ответ AI → edit() с заголовком 'OCR:'."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            yield "INVOICE\nTotal: 100.00"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_called_once()
        call_text = status_msg.edit.call_args[0][0]
        assert "OCR" in call_text
        assert "INVOICE" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """AI вернул пустой ответ → edit() с сообщением об ошибке."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_тоже_ошибка(self) -> None:
        """Whitespace-только ответ → сообщение об ошибке."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            yield "   \n   "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_streaming_чанки_склеиваются(self) -> None:
        """Несколько чанков → склеиваются в один ответ."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            yield "Строка 1\n"
            yield "Строка 2\n"
            yield "Строка 3"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "Строка 1" in call_text
        assert "Строка 2" in call_text
        assert "Строка 3" in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с сообщением об ошибке."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            raise RuntimeError("network error")
            yield

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_exception_текст_ошибки_виден_пользователю(self) -> None:
        """Текст исключения отображается пользователю."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        async def fake_stream(message, chat_id, **_kw):
            raise ValueError("vision_quota_exceeded")
            yield

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "vision_quota_exceeded" in call_text


# ===========================================================================
# Статусное сообщение
# ===========================================================================


class TestHandleOcrStatusMessage:
    """Поведение статусного сообщения."""

    @pytest.mark.asyncio
    async def test_статусное_сообщение_отправляется_первым(self) -> None:
        """До обращения к AI отправляется статусное 'Извлекаю...'."""
        img_bytes = b"\xff\xd8\xff"
        call_order: list[str] = []

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        original_reply = msg.reply

        async def tracking_reply(text, *args, **kwargs):
            call_order.append(f"reply:{text[:20]}")
            return await original_reply(text, *args, **kwargs)

        msg.reply = tracking_reply

        async def fake_stream(message, chat_id, **_kw):
            call_order.append("stream_called")
            yield "Текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        # Статусное сообщение должно быть отправлено ДО вызова stream
        assert call_order[0].startswith("reply:")
        assert "stream_called" in call_order
        assert call_order.index("stream_called") > 0

    @pytest.mark.asyncio
    async def test_статусное_сообщение_содержит_извлеч(self) -> None:
        """Статусное сообщение содержит слово 'Извлек' или '🔍'."""
        img_bytes = b"\xff\xd8\xff"

        async def fake_download(in_memory=None):
            if in_memory is not None:
                in_memory.write(img_bytes)
            return in_memory

        replied = SimpleNamespace(
            photo=SimpleNamespace(file_id="x"),
            document=None,
            download=fake_download,
        )
        bot, msg = _make_message(replied=replied)

        first_reply_text: list[str] = []

        async def tracking_reply(text, *args, **kwargs):
            first_reply_text.append(text)
            edit_mock = AsyncMock()
            return SimpleNamespace(edit=edit_mock)

        msg.reply = AsyncMock(side_effect=tracking_reply)

        async def fake_stream(message, chat_id, **_kw):
            yield "Текст"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_ocr(bot, msg)

        assert first_reply_text
        text = first_reply_text[0].lower()
        assert "извлек" in text or "🔍" in first_reply_text[0] or "ocr" in text


# ===========================================================================
# Экспорт и registry
# ===========================================================================


class TestHandleOcrExport:
    """handle_ocr должен быть экспортируемым."""

    def test_handle_ocr_importable(self) -> None:
        """handle_ocr импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_ocr  # noqa: F401

        assert callable(handle_ocr)

    def test_handle_ocr_async(self) -> None:
        """handle_ocr — корутинная функция."""
        import asyncio

        from src.handlers.command_handlers import handle_ocr

        assert asyncio.iscoroutinefunction(handle_ocr)


class TestHandleOcrRegistry:
    """!ocr зарегистрирована в command_registry."""

    def test_ocr_в_реестре(self) -> None:
        """Команда 'ocr' присутствует в реестре."""
        from src.core.command_registry import registry

        cmd = registry.get("ocr")
        assert cmd is not None

    def test_ocr_категория_ai(self) -> None:
        """Команда 'ocr' в категории 'ai'."""
        from src.core.command_registry import registry

        cmd = registry.get("ocr")
        assert cmd is not None
        assert cmd.category == "ai"

    def test_ocr_в_списке_по_категории(self) -> None:
        """by_category('ai') содержит команду 'ocr'."""
        from src.core.command_registry import registry

        ai_cmds = [c.name for c in registry.by_category("ai")]
        assert "ocr" in ai_cmds

    def test_ocr_owner_only(self) -> None:
        """!ocr только для owner."""
        from src.core.command_registry import registry

        cmd = registry.get("ocr")
        assert cmd is not None
        assert cmd.owner_only is True

    def test_ocr_usage_содержит_команду(self) -> None:
        """usage содержит !ocr."""
        from src.core.command_registry import registry

        cmd = registry.get("ocr")
        assert cmd is not None
        assert "ocr" in cmd.usage.lower()

    def test_ocr_description_осмысленное(self) -> None:
        """description не пустое."""
        from src.core.command_registry import registry

        cmd = registry.get("ocr")
        assert cmd is not None
        assert len(cmd.description) > 5
