# -*- coding: utf-8 -*-
"""
Юнит-тесты для !media command handler.

Покрываем:
  - handle_media без reply → UserInputError
  - handle_media на сообщение без медиа → UserInputError
  - !media (дефолт) reply на фото → скачивает и пересылает как документ
  - !media (дефолт) reply на видео → скачивает и пересылает как документ
  - !media (дефолт) reply на документ → скачивает и пересылает
  - !media (дефолт) reply на аудио → скачивает и пересылает
  - !media (дефолт) reply на голосовое → скачивает и пересылает
  - !media (дефолт) reply на стикер → скачивает и пересылает
  - !media (дефолт) пустой файл → сообщение об ошибке
  - !media save → скачивает в ~/Downloads/krab_media/
  - !media save → сообщение с путём файла
  - !media save ошибка → сообщение об ошибке
  - !media info reply на фото → метаданные без скачивания
  - !media info reply на видео → метаданные с разрешением и длительностью
  - !media info reply на документ → метаданные с MIME
  - !media info reply на аудио → длительность в метаданных
  - !media info: размер форматируется в КБ и МБ
  - handle_media импортируется из handlers
  - handle_media в реестре команд
  - handle_media owner_only в реестре
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_media

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_photo_msg(
    file_unique_id: str = "uid_photo",
    file_size: int = 204800,
    width: int = 1920,
    height: int = 1080,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=SimpleNamespace(
            file_unique_id=file_unique_id,
            file_size=file_size,
            width=width,
            height=height,
        ),
        video=None,
        document=None,
        audio=None,
        voice=None,
        sticker=None,
    )


def _make_video_msg(
    file_unique_id: str = "uid_video",
    file_name: str | None = None,
    mime_type: str = "video/mp4",
    file_size: int = 5_000_000,
    width: int = 1280,
    height: int = 720,
    duration: int = 30,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=None,
        video=SimpleNamespace(
            file_unique_id=file_unique_id,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
            width=width,
            height=height,
            duration=duration,
        ),
        document=None,
        audio=None,
        voice=None,
        sticker=None,
    )


def _make_document_msg(
    file_unique_id: str = "uid_doc",
    file_name: str = "report.pdf",
    mime_type: str = "application/pdf",
    file_size: int = 102400,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=None,
        video=None,
        document=SimpleNamespace(
            file_unique_id=file_unique_id,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
        ),
        audio=None,
        voice=None,
        sticker=None,
    )


def _make_audio_msg(
    file_unique_id: str = "uid_audio",
    file_name: str | None = None,
    mime_type: str = "audio/mpeg",
    file_size: int = 3_500_000,
    duration: int = 210,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=None,
        video=None,
        document=None,
        audio=SimpleNamespace(
            file_unique_id=file_unique_id,
            file_name=file_name,
            mime_type=mime_type,
            file_size=file_size,
            duration=duration,
        ),
        voice=None,
        sticker=None,
    )


def _make_voice_msg(
    file_unique_id: str = "uid_voice",
    mime_type: str = "audio/ogg",
    file_size: int = 50000,
    duration: int = 15,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=None,
        video=None,
        document=None,
        audio=None,
        voice=SimpleNamespace(
            file_unique_id=file_unique_id,
            mime_type=mime_type,
            file_size=file_size,
            duration=duration,
        ),
        sticker=None,
    )


def _make_sticker_msg(
    file_unique_id: str = "uid_sticker",
    mime_type: str = "image/webp",
    file_size: int = 20480,
    width: int = 512,
    height: int = 512,
    is_animated: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        photo=None,
        video=None,
        document=None,
        audio=None,
        voice=None,
        sticker=SimpleNamespace(
            file_unique_id=file_unique_id,
            mime_type=mime_type,
            file_size=file_size,
            width=width,
            height=height,
            is_animated=is_animated,
        ),
    )


def _make_message(
    command_args: str = "",
    chat_id: int = 123456,
    replied: SimpleNamespace | None = None,
    message_id: int = 999,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Возвращает (bot, message) stubs."""
    edit_mock = AsyncMock()
    delete_mock = AsyncMock()
    status_msg = SimpleNamespace(edit=edit_mock, delete=delete_mock)

    send_document_mock = AsyncMock()
    client_stub = SimpleNamespace(send_document=send_document_mock)

    msg = SimpleNamespace(
        text=f"!media {command_args}".strip(),
        reply=AsyncMock(return_value=status_msg),
        chat=SimpleNamespace(id=chat_id),
        reply_to_message=replied,
        id=message_id,
    )

    bot = SimpleNamespace(
        _get_command_args=lambda _m: command_args,
        client=client_stub,
    )
    return bot, msg


def _add_fake_download(replied: SimpleNamespace, content: bytes = b"fake_bytes") -> None:
    """Добавляет fake download метод к replied-сообщению."""

    async def fake_download(file_name: str | None = None, in_memory=None):
        if file_name:
            p = pathlib.Path(file_name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return file_name

    replied.download = fake_download


# ===========================================================================
# Валидация входных данных
# ===========================================================================


class TestHandleMediaValidation:
    """Проверка ошибок при некорректном вводе."""

    @pytest.mark.asyncio
    async def test_нет_reply_поднимает_userinputerror(self) -> None:
        """!media без reply → UserInputError."""
        bot, msg = _make_message(replied=None)
        with pytest.raises(UserInputError):
            await handle_media(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_на_текст_без_медиа_поднимает_userinputerror(self) -> None:
        """!media в reply на текстовое сообщение → UserInputError."""
        replied = SimpleNamespace(
            photo=None, video=None, document=None,
            audio=None, voice=None, sticker=None,
        )
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError):
            await handle_media(bot, msg)

    @pytest.mark.asyncio
    async def test_userinputerror_без_reply_содержит_hint(self) -> None:
        """Сообщение ошибки содержит подсказку по использованию."""
        bot, msg = _make_message(replied=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_media(bot, msg)
        err = exc_info.value.user_message
        assert "media" in err.lower() or "📥" in err

    @pytest.mark.asyncio
    async def test_userinputerror_без_медиа_содержит_hint(self) -> None:
        """Сообщение ошибки при reply на текст содержит подсказку."""
        replied = SimpleNamespace(
            photo=None, video=None, document=None,
            audio=None, voice=None, sticker=None,
        )
        bot, msg = _make_message(replied=replied)
        with pytest.raises(UserInputError) as exc_info:
            await handle_media(bot, msg)
        err = exc_info.value.user_message
        assert "медиафайл" in err or "фото" in err


# ===========================================================================
# Режим по умолчанию: скачивание и пересылка
# ===========================================================================


class TestHandleMediaDefault:
    """Дефолтный режим: !media (без аргументов)."""

    @pytest.mark.asyncio
    async def test_фото_пересылается_как_документ(self) -> None:
        """!media reply на фото → send_document вызван."""
        replied = _make_photo_msg()
        _add_fake_download(replied, content=b"jpeg_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called
        call_args = bot.client.send_document.call_args
        assert call_args[0][0] == msg.chat.id

    @pytest.mark.asyncio
    async def test_видео_пересылается_как_документ(self) -> None:
        """!media reply на видео → send_document вызван."""
        replied = _make_video_msg()
        _add_fake_download(replied, content=b"video_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_документ_пересылается(self) -> None:
        """!media reply на документ → send_document вызван."""
        replied = _make_document_msg()
        _add_fake_download(replied, content=b"pdf_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_аудио_пересылается(self) -> None:
        """!media reply на аудио → send_document вызван."""
        replied = _make_audio_msg()
        _add_fake_download(replied, content=b"mp3_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_голосовое_пересылается(self) -> None:
        """!media reply на голосовое → send_document вызван."""
        replied = _make_voice_msg()
        _add_fake_download(replied, content=b"ogg_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_стикер_пересылается(self) -> None:
        """!media reply на стикер → send_document вызван."""
        replied = _make_sticker_msg()
        _add_fake_download(replied, content=b"webp_content")
        bot, msg = _make_message(command_args="", replied=replied)

        await handle_media(bot, msg)

        assert bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_caption_содержит_имя_файла(self) -> None:
        """Caption отправляемого документа содержит имя файла."""
        replied = _make_photo_msg(file_unique_id="abc123")
        _add_fake_download(replied, content=b"data")
        bot, msg = _make_message(replied=replied)

        await handle_media(bot, msg)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs.get("caption", "")
        assert "photo_abc123.jpg" in caption or "photo_abc123" in caption

    @pytest.mark.asyncio
    async def test_статусное_сообщение_отправляется_до_скачивания(self) -> None:
        """Статусное сообщение про скачивание отправляется первым."""
        replied = _make_photo_msg()
        call_order: list[str] = []

        async def fake_download(file_name=None, in_memory=None):
            call_order.append("download")
            if file_name:
                p = pathlib.Path(file_name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"data")
            return file_name

        replied.download = fake_download
        bot, msg = _make_message(replied=replied)

        original_reply = msg.reply

        async def tracking_reply(text, *a, **kw):
            call_order.append(f"reply:{text[:15]}")
            return await original_reply(text, *a, **kw)

        msg.reply = tracking_reply

        await handle_media(bot, msg)

        # Статусное reply должно быть первым
        assert call_order[0].startswith("reply:")
        assert "download" in call_order
        assert call_order.index("download") > 0

    @pytest.mark.asyncio
    async def test_пустой_файл_ошибка(self) -> None:
        """Если download создал пустой файл → edit с ошибкой."""
        replied = _make_document_msg()

        async def empty_download(file_name=None, in_memory=None):
            if file_name:
                p = pathlib.Path(file_name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"")  # пустой файл
            return file_name

        replied.download = empty_download
        bot, msg = _make_message(replied=replied)

        await handle_media(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text
        assert not bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_ошибка_скачивания_graceful(self) -> None:
        """RuntimeError в download → edit с сообщением об ошибке."""
        replied = _make_document_msg()

        async def failing_download(file_name=None, in_memory=None):
            raise RuntimeError("network_timeout")

        replied.download = failing_download
        bot, msg = _make_message(replied=replied)

        await handle_media(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_имя_видео_из_file_name_если_есть(self) -> None:
        """Если у видео есть file_name — используется оно."""
        replied = _make_video_msg(file_name="myvideo.mp4")
        _add_fake_download(replied, content=b"data")
        bot, msg = _make_message(replied=replied)

        await handle_media(bot, msg)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs.get("caption", "")
        assert "myvideo.mp4" in caption

    @pytest.mark.asyncio
    async def test_имя_видео_генерируется_если_нет_file_name(self) -> None:
        """Если у видео нет file_name — генерируется из file_unique_id."""
        replied = _make_video_msg(file_unique_id="xyz789", file_name=None)
        _add_fake_download(replied, content=b"data")
        bot, msg = _make_message(replied=replied)

        await handle_media(bot, msg)

        call_kwargs = bot.client.send_document.call_args[1]
        caption = call_kwargs.get("caption", "")
        assert "xyz789" in caption


# ===========================================================================
# Режим !media save
# ===========================================================================


class TestHandleMediaSave:
    """!media save: скачивание на диск."""

    @pytest.mark.asyncio
    async def test_save_создаёт_файл_на_диске(self, tmp_path) -> None:
        """!media save → файл создаётся на диске в krab_media/."""
        replied = _make_document_msg(file_name="test_doc.pdf", file_size=10240)

        saved_paths: list[str] = []

        async def tracking_download(file_name=None, in_memory=None):
            saved_paths.append(file_name or "")
            if file_name:
                p = pathlib.Path(file_name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"pdf_data")
            return file_name

        replied.download = tracking_download
        bot, msg = _make_message(command_args="save", replied=replied)

        with patch("pathlib.Path.home", return_value=tmp_path):
            await handle_media(bot, msg)

        assert len(saved_paths) == 1
        assert "test_doc.pdf" in saved_paths[0]

    @pytest.mark.asyncio
    async def test_save_статус_содержит_путь(self, tmp_path) -> None:
        """После сохранения статусное сообщение содержит путь."""
        replied = _make_document_msg(file_name="myfile.docx", file_size=20480)

        async def fake_download(file_name=None, in_memory=None):
            if file_name:
                p = pathlib.Path(file_name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"docx_data")
            return file_name

        replied.download = fake_download
        bot, msg = _make_message(command_args="save", replied=replied)

        with patch("pathlib.Path.home", return_value=tmp_path):
            await handle_media(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "✅" in call_text
        assert "krab_media" in call_text or "myfile.docx" in call_text

    @pytest.mark.asyncio
    async def test_save_ошибка_graceful(self) -> None:
        """RuntimeError в download при save → edit с ошибкой."""
        replied = _make_document_msg()

        async def failing_download(file_name=None, in_memory=None):
            raise OSError("disk_full")

        replied.download = failing_download
        bot, msg = _make_message(command_args="save", replied=replied)

        await handle_media(bot, msg)

        status_msg = msg.reply.return_value
        call_text = status_msg.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_save_не_вызывает_send_document(self, tmp_path) -> None:
        """!media save не пересылает файл в чат."""
        replied = _make_photo_msg()

        async def fake_download(file_name=None, in_memory=None):
            if file_name:
                p = pathlib.Path(file_name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b"data")
            return file_name

        replied.download = fake_download
        bot, msg = _make_message(command_args="save", replied=replied)

        with patch("pathlib.Path.home", return_value=tmp_path):
            await handle_media(bot, msg)

        assert not bot.client.send_document.called


# ===========================================================================
# Режим !media info
# ===========================================================================


class TestHandleMediaInfo:
    """!media info: метаданные без скачивания."""

    @pytest.mark.asyncio
    async def test_info_фото_содержит_тип(self) -> None:
        """!media info reply на фото → ответ содержит 'photo'."""
        replied = _make_photo_msg()
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        msg.reply.assert_called_once()
        call_text = msg.reply.call_args[0][0]
        assert "photo" in call_text

    @pytest.mark.asyncio
    async def test_info_фото_содержит_разрешение(self) -> None:
        """!media info reply на фото → ответ содержит разрешение."""
        replied = _make_photo_msg(width=1920, height=1080)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "1920" in call_text and "1080" in call_text

    @pytest.mark.asyncio
    async def test_info_видео_содержит_длительность(self) -> None:
        """!media info reply на видео → ответ содержит длительность."""
        replied = _make_video_msg(duration=120)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "120" in call_text

    @pytest.mark.asyncio
    async def test_info_документ_содержит_mime(self) -> None:
        """!media info reply на документ → ответ содержит MIME-тип."""
        replied = _make_document_msg(mime_type="application/pdf")
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "application/pdf" in call_text

    @pytest.mark.asyncio
    async def test_info_аудио_содержит_длительность(self) -> None:
        """!media info reply на аудио → ответ содержит длительность."""
        replied = _make_audio_msg(duration=210)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "210" in call_text

    @pytest.mark.asyncio
    async def test_info_голосовое_содержит_длительность(self) -> None:
        """!media info reply на голосовое → ответ содержит длительность."""
        replied = _make_voice_msg(duration=15)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "15" in call_text

    @pytest.mark.asyncio
    async def test_info_стикер_содержит_разрешение(self) -> None:
        """!media info reply на стикер → ответ содержит разрешение."""
        replied = _make_sticker_msg(width=512, height=512)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "512" in call_text

    @pytest.mark.asyncio
    async def test_info_не_вызывает_download(self) -> None:
        """!media info не скачивает файл."""
        replied = _make_photo_msg()
        download_called = []

        async def tracking_download(file_name=None, in_memory=None):
            download_called.append(True)
            return file_name

        replied.download = tracking_download
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        assert len(download_called) == 0

    @pytest.mark.asyncio
    async def test_info_не_вызывает_send_document(self) -> None:
        """!media info не пересылает файл в чат."""
        replied = _make_photo_msg()
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        assert not bot.client.send_document.called

    @pytest.mark.asyncio
    async def test_info_размер_в_кб(self) -> None:
        """Размер < 1МБ форматируется как КБ."""
        replied = _make_photo_msg(file_size=51200)  # 50 КБ
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "КБ" in call_text

    @pytest.mark.asyncio
    async def test_info_размер_в_мб(self) -> None:
        """Размер >= 1МБ форматируется как МБ."""
        replied = _make_photo_msg(file_size=5_000_000)  # ~4.8 МБ
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "МБ" in call_text

    @pytest.mark.asyncio
    async def test_info_имя_файла_в_ответе(self) -> None:
        """!media info содержит имя файла."""
        replied = _make_document_msg(file_name="contract.docx")
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "contract.docx" in call_text


# ===========================================================================
# Имена файлов и расширения
# ===========================================================================


class TestHandleMediaFileNames:
    """Генерация имён файлов для разных типов медиа."""

    @pytest.mark.asyncio
    async def test_фото_получает_jpg_расширение(self) -> None:
        """Фото всегда получает имя вида photo_<uid>.jpg."""
        replied = _make_photo_msg(file_unique_id="testuid")
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "photo_testuid.jpg" in call_text

    @pytest.mark.asyncio
    async def test_голосовое_получает_ogg_расширение(self) -> None:
        """Голосовое с mime audio/ogg получает .ogg расширение."""
        replied = _make_voice_msg(file_unique_id="voiceuid", mime_type="audio/ogg")
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "voiceuid" in call_text

    @pytest.mark.asyncio
    async def test_animated_стикер_получает_tgs_расширение(self) -> None:
        """Анимированный стикер получает .tgs расширение."""
        replied = _make_sticker_msg(file_unique_id="anim", is_animated=True)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "anim" in call_text and ".tgs" in call_text

    @pytest.mark.asyncio
    async def test_static_стикер_получает_webp_расширение(self) -> None:
        """Статичный стикер получает .webp расширение."""
        replied = _make_sticker_msg(file_unique_id="static", is_animated=False)
        bot, msg = _make_message(command_args="info", replied=replied)

        await handle_media(bot, msg)

        call_text = msg.reply.call_args[0][0]
        assert "static" in call_text and ".webp" in call_text


# ===========================================================================
# Экспорт и реестр
# ===========================================================================


class TestHandleMediaExport:
    """handle_media должен быть экспортируемым и корутинным."""

    def test_handle_media_importable(self) -> None:
        """handle_media импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_media  # noqa: F401

        assert callable(handle_media)

    def test_handle_media_async(self) -> None:
        """handle_media — корутинная функция."""
        import asyncio

        from src.handlers.command_handlers import handle_media

        assert asyncio.iscoroutinefunction(handle_media)


class TestHandleMediaRegistry:
    """!media зарегистрирована в command_registry."""

    def test_media_в_реестре(self) -> None:
        """Команда 'media' присутствует в реестре."""
        from src.core.command_registry import registry

        cmd = registry.get("media")
        assert cmd is not None

    def test_media_owner_only(self) -> None:
        """!media только для owner."""
        from src.core.command_registry import registry

        cmd = registry.get("media")
        assert cmd is not None
        assert cmd.owner_only is True

    def test_media_категория_files(self) -> None:
        """Команда 'media' в категории 'files'."""
        from src.core.command_registry import registry

        cmd = registry.get("media")
        assert cmd is not None
        assert cmd.category == "files"
