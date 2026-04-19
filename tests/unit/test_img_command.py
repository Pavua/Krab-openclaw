# -*- coding: utf-8 -*-
"""
Юнит-тесты для !img command handler.

Покрываем:
  - handle_img без reply → UserInputError
  - handle_img reply на не-фото → UserInputError
  - handle_img reply на фото → скачивает, base64, отправляет в vision
  - handle_img reply на документ-изображение → работает
  - !img без вопроса → дефолтный промпт
  - !img <вопрос> → вопрос как промпт
  - session_id = img_{chat_id}
  - force_cloud=True обязательно
  - disable_tools=True
  - пустой ответ AI → сообщение об ошибке
  - streaming-чанки склеиваются
  - исключение в openclaw → graceful error
  - пустой download → сообщение об ошибке
  - handle_img экспортируется из handlers
  - !img в реестре команд
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_img

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_photo_replied(with_photo: bool = True, with_doc_image: bool = False) -> SimpleNamespace:
    """Создаёт stub сообщения с фото (или нет)."""
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
        text=f"!img {command_args}".strip(),
        reply=AsyncMock(return_value=status_msg),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=replied,
    )

    bot = SimpleNamespace(_get_command_args=lambda _m: command_args)
    return bot, msg


# ===========================================================================
# Ошибочные входные данные
# ===========================================================================


class TestHandleImgValidation:
    """Валидация входных данных."""

    @pytest.mark.asyncio
    async def test_нет_reply_поднимает_userinputerror(self) -> None:
        """!img без reply → UserInputError."""
        bot, msg = _make_message(command_args="", replied=None)
        with pytest.raises(UserInputError):
            await handle_img(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_на_текст_поднимает_userinputerror(self) -> None:
        """!img в reply на текстовое сообщение → UserInputError."""
        replied = SimpleNamespace(photo=None, document=None)
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError):
            await handle_img(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_на_не_image_документ_поднимает_userinputerror(self) -> None:
        """!img в reply на PDF-документ → UserInputError."""
        replied = SimpleNamespace(
            photo=None,
            document=SimpleNamespace(mime_type="application/pdf", file_id="pdf_id"),
        )
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError):
            await handle_img(bot, msg)

    @pytest.mark.asyncio
    async def test_userinputerror_без_reply_содержит_hint(self) -> None:
        """Сообщение ошибки содержит подсказку."""
        bot, msg = _make_message(replied=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_img(bot, msg)
        assert "img" in exc_info.value.user_message.lower() or "фото" in exc_info.value.user_message


# ===========================================================================
# Скачивание и base64
# ===========================================================================


class TestHandleImgDownload:
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
            yield "Это кот на фото."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

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

        await handle_img(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_документ_изображение_принимается(self) -> None:
        """!img reply на документ image/jpeg → тоже обрабатывается."""
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
            yield "Описание документа-изображения."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_called_once()
        assert "Описание" in status_msg.edit.call_args[0][0]


# ===========================================================================
# Промпт
# ===========================================================================


class TestHandleImgPrompt:
    """Формирование промпта."""

    @pytest.mark.asyncio
    async def test_без_вопроса_дефолтный_промпт_на_описание(self) -> None:
        """!img без аргументов → промпт про описание."""
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
            yield "Описание"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert len(captured) == 1
        prompt = captured[0].lower()
        # Дефолтный промпт содержит слово "опиши" или аналог
        assert "опиши" in prompt or "describe" in prompt or "изображ" in prompt

    @pytest.mark.asyncio
    async def test_с_вопросом_вопрос_идёт_как_промпт(self) -> None:
        """!img Что написано? → именно этот вопрос как промпт."""
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
        bot, msg = _make_message(command_args="Что написано?", replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, images=None, **_kw):
            captured.append(message)
            yield "Написано: Hello"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert "Что написано?" in captured[0]

    @pytest.mark.asyncio
    async def test_вопрос_не_содержит_дефолтный_промпт(self) -> None:
        """Когда вопрос задан, дефолтный промпт не используется."""
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
        bot, msg = _make_message(command_args="Сколько людей?", replied=replied)

        captured: list[str] = []

        async def fake_stream(message, chat_id, images=None, **_kw):
            captured.append(message)
            yield "3 человека"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        # Дефолтный текст "Опиши это фото" не должен быть в промпте
        assert "Опиши это фото" not in captured[0]


# ===========================================================================
# Параметры вызова OpenClaw
# ===========================================================================


class TestHandleImgOpenClawParams:
    """Параметры, передаваемые в send_message_stream."""

    @pytest.mark.asyncio
    async def test_force_cloud_true(self) -> None:
        """!img всегда вызывает send_message_stream с force_cloud=True."""
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
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert captured[0]["force_cloud"] is True

    @pytest.mark.asyncio
    async def test_disable_tools_true(self) -> None:
        """!img вызывает send_message_stream с disable_tools=True."""
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
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert captured[0]["disable_tools"] is True

    @pytest.mark.asyncio
    async def test_session_id_изолирован_по_chat_id(self) -> None:
        """chat_id передаётся как 'img_{chat_id}'."""
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
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert captured_sessions[0] == "img_99887"

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
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            replied1 = SimpleNamespace(
                photo=SimpleNamespace(file_id="x"), document=None, download=fake_download
            )
            bot1, msg1 = _make_message(chat_id=111, replied=replied1)
            await handle_img(bot1, msg1)

            replied2 = SimpleNamespace(
                photo=SimpleNamespace(file_id="y"), document=None, download=fake_download
            )
            bot2, msg2 = _make_message(chat_id=222, replied=replied2)
            await handle_img(bot2, msg2)

        assert sessions[0] == "img_111"
        assert sessions[1] == "img_222"
        assert sessions[0] != sessions[1]


# ===========================================================================
# Обработка ответа AI
# ===========================================================================


class TestHandleImgResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_статусное_сообщение(self) -> None:
        """Ответ AI → edit() статусного сообщения."""
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
            yield "Красивое фото с закатом над горами."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        status_msg = msg.reply.return_value
        status_msg.edit.assert_called_once()
        call_text = status_msg.edit.call_args[0][0]
        assert "Красивое фото" in call_text

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
            await handle_img(bot, msg)

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
            await handle_img(bot, msg)

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
            yield "Часть 1. "
            yield "Часть 2. "
            yield "Часть 3."

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "Часть 1. Часть 2. Часть 3." in call_text

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
            await handle_img(bot, msg)

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
            raise ValueError("vision_not_supported")
            yield

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "vision_not_supported" in call_text


# ===========================================================================
# Статусное сообщение
# ===========================================================================


class TestHandleImgStatusMessage:
    """Поведение статусного сообщения."""

    @pytest.mark.asyncio
    async def test_статусное_сообщение_отправляется_первым(self) -> None:
        """До обращения к AI отправляется статусное 'Анализирую...'."""
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

        # Перехватываем reply — до вызова stream
        original_reply = msg.reply

        async def tracking_reply(text, *args, **kwargs):
            call_order.append(f"reply:{text[:20]}")
            return await original_reply(text, *args, **kwargs)

        msg.reply = tracking_reply

        async def fake_stream(message, chat_id, **_kw):
            call_order.append("stream_called")
            yield "Ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        # Статусное сообщение должно быть отправлено ДО вызова stream
        assert call_order[0].startswith("reply:🔍")
        assert "stream_called" in call_order
        assert call_order.index("stream_called") > call_order.index(call_order[0])

    @pytest.mark.asyncio
    async def test_статусное_сообщение_содержит_анализ(self) -> None:
        """Статусное сообщение содержит слово 'Анализ' или аналог."""
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
        original_reply = msg.reply.side_effect

        async def tracking_reply(text, *args, **kwargs):
            first_reply_text.append(text)
            edit_mock = AsyncMock()
            return SimpleNamespace(edit=edit_mock)

        msg.reply = AsyncMock(side_effect=tracking_reply)

        async def fake_stream(message, chat_id, **_kw):
            yield "Ответ AI"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_img(bot, msg)

        assert first_reply_text
        assert "анализ" in first_reply_text[0].lower() or "🔍" in first_reply_text[0]


# ===========================================================================
# Экспорт и registry
# ===========================================================================


class TestHandleImgExport:
    """handle_img должен быть экспортируемым."""

    def test_handle_img_importable(self) -> None:
        """handle_img импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_img  # noqa: F401

        assert callable(handle_img)

    def test_handle_img_async(self) -> None:
        """handle_img — корутинная функция."""
        import asyncio

        from src.handlers.command_handlers import handle_img

        assert asyncio.iscoroutinefunction(handle_img)


class TestHandleImgRegistry:
    """!img зарегистрирована в command_registry."""

    def test_img_в_реестре(self) -> None:
        """Команда 'img' присутствует в реестре."""
        from src.core.command_registry import registry

        cmd = registry.get("img")
        assert cmd is not None

    def test_img_категория_ai(self) -> None:
        """Команда 'img' в категории 'ai'."""
        from src.core.command_registry import registry

        cmd = registry.get("img")
        assert cmd is not None
        assert cmd.category == "ai"

    def test_img_в_списке_по_категории(self) -> None:
        """by_category('ai') содержит команду 'img'."""
        from src.core.command_registry import registry

        ai_cmds = [c.name for c in registry.by_category("ai")]
        assert "img" in ai_cmds

    def test_img_owner_only(self) -> None:
        """!img только для owner."""
        from src.core.command_registry import registry

        cmd = registry.get("img")
        assert cmd is not None
        assert cmd.owner_only is True
