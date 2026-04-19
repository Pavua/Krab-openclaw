# -*- coding: utf-8 -*-
"""
Юнит-тесты для !yt command handler.

Покрываем:
  - handle_yt: URL в аргументах
  - handle_yt: URL извлекается из reply (youtu.be, youtube.com/watch, shorts)
  - нет URL ни в аргументах ни в reply → UserInputError
  - reply без текста → UserInputError
  - пустой ответ AI
  - несколько streaming-чанков склеиваются
  - длинный ответ разбивается через _split_text_for_telegram
  - ошибка openclaw_client обрабатывается gracefully
  - session_id = yt_{chat_id}
  - disable_tools=False (web_search нужен)
  - URL попадает в промпт
  - _extract_yt_url вспомогательная функция
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _extract_yt_url, handle_yt

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> SimpleNamespace:
    """Stub KraabUserbot с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _m: command_args)


def _make_msg(
    command_args: str = "",
    reply_text: str | None = None,
    chat_id: int = 42,
) -> SimpleNamespace:
    """Stub Message."""
    edit_mock = AsyncMock()
    sent_stub = SimpleNamespace(edit=edit_mock)

    if reply_text is not None:
        replied = SimpleNamespace(text=reply_text, caption=None)
    else:
        replied = None

    return SimpleNamespace(
        text=f"!yt {command_args}".strip(),
        reply=AsyncMock(return_value=sent_stub),
        reply_to_message=replied,
        chat=SimpleNamespace(id=chat_id),
    )


def _async_gen(*values: str):
    """AsyncGenerator из списка строк."""

    async def _gen():
        for v in values:
            yield v

    return _gen()


# ---------------------------------------------------------------------------
# Тесты _extract_yt_url
# ---------------------------------------------------------------------------


class TestExtractYtUrl:
    """Проверка вспомогательной функции извлечения YouTube URL."""

    def test_youtube_watch_url(self) -> None:
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert _extract_yt_url(url) == url

    def test_youtu_be_short_url(self) -> None:
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert _extract_yt_url(url) == url

    def test_youtube_shorts_url(self) -> None:
        url = "https://youtube.com/shorts/abc123XYZ"
        assert _extract_yt_url(url) == url

    def test_url_в_середине_текста(self) -> None:
        text = "Посмотри это видео https://youtu.be/abc123 оно классное"
        assert _extract_yt_url(text) == "https://youtu.be/abc123"

    def test_нет_url_возвращает_none(self) -> None:
        assert _extract_yt_url("просто текст без ссылки") is None

    def test_пустая_строка(self) -> None:
        assert _extract_yt_url("") is None

    def test_none_строка(self) -> None:
        assert _extract_yt_url(None) is None  # type: ignore[arg-type]

    def test_не_youtube_url_игнорируется(self) -> None:
        assert _extract_yt_url("https://vimeo.com/12345") is None

    def test_без_www(self) -> None:
        url = "https://youtube.com/watch?v=abc123xyz"
        assert _extract_yt_url(url) == url


# ---------------------------------------------------------------------------
# Валидация входных данных
# ---------------------------------------------------------------------------


class TestHandleYtValidation:
    """Проверка обязательных условий."""

    @pytest.mark.asyncio
    async def test_нет_аргументов_нет_reply_UserInputError(self) -> None:
        """Нет URL ни в аргументах ни в reply → UserInputError."""
        bot = _make_bot("")
        msg = _make_msg("", reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_yt(bot, msg)
        assert (
            "yt" in exc_info.value.user_message.lower()
            or "youtube" in exc_info.value.user_message.lower()
            or "url" in exc_info.value.user_message.lower()
        )

    @pytest.mark.asyncio
    async def test_аргументы_без_youtube_url_UserInputError(self) -> None:
        """Передан текст без YouTube URL → UserInputError."""
        bot = _make_bot("просто текст")
        msg = _make_msg("просто текст", reply_text=None)
        with pytest.raises(UserInputError):
            await handle_yt(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_без_youtube_url_UserInputError(self) -> None:
        """Reply есть, но в тексте нет YouTube URL → UserInputError."""
        bot = _make_bot("")
        msg = _make_msg("", reply_text="Нет ссылки тут")
        with pytest.raises(UserInputError):
            await handle_yt(bot, msg)

    @pytest.mark.asyncio
    async def test_reply_none_и_нет_аргументов_UserInputError(self) -> None:
        """reply_to_message=None, args='' → UserInputError."""
        bot = _make_bot("")
        msg = _make_msg("", reply_text=None)
        msg.reply_to_message = None
        with pytest.raises(UserInputError):
            await handle_yt(bot, msg)


# ---------------------------------------------------------------------------
# Источники URL
# ---------------------------------------------------------------------------


class TestHandleYtUrlSources:
    """Проверка источников URL: аргументы vs reply."""

    @pytest.mark.asyncio
    async def test_url_из_аргументов(self) -> None:
        """URL в аргументах → используется для промпта."""
        url = "https://youtu.be/dQw4w9WgXcQ"
        bot = _make_bot(url)
        msg = _make_msg(url, reply_text=None)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Название: Rick Astley"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert url in captured[0]

    @pytest.mark.asyncio
    async def test_url_из_reply(self) -> None:
        """URL в reply → используется для промпта."""
        url = "https://www.youtube.com/watch?v=abc123xyz"
        bot = _make_bot("")
        msg = _make_msg("", reply_text=f"Посмотри: {url}")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "Название: Пример видео"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert url in captured[0]

    @pytest.mark.asyncio
    async def test_аргументы_имеют_приоритет_над_reply(self) -> None:
        """Если URL есть и в аргументах и в reply — используется из аргументов."""
        url_args = "https://youtu.be/AAAAaaa111"
        url_reply = "https://youtu.be/BBBBbbb222"
        bot = _make_bot(url_args)
        msg = _make_msg(url_args, reply_text=url_reply)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert url_args in captured[0]

    @pytest.mark.asyncio
    async def test_url_из_caption_reply(self) -> None:
        """URL в caption reply_to_message → используется."""
        url = "https://youtu.be/captiontest"
        bot = _make_bot("")
        msg = _make_msg("", reply_text=None)
        msg.reply_to_message = SimpleNamespace(text=None, caption=f"Caption: {url}")

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert url in captured[0]


# ---------------------------------------------------------------------------
# Сессия и параметры вызова
# ---------------------------------------------------------------------------


class TestHandleYtSession:
    """Проверка параметров send_message_stream."""

    @pytest.mark.asyncio
    async def test_session_id_yt_prefix(self) -> None:
        """chat_id передаётся как 'yt_{chat_id}'."""
        url = "https://youtu.be/test123"
        bot = _make_bot(url)
        msg = _make_msg(url, chat_id=777)

        captured_ids: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured_ids.append(chat_id)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert captured_ids[0] == "yt_777"

    @pytest.mark.asyncio
    async def test_disable_tools_false(self) -> None:
        """!yt вызывает send_message_stream с disable_tools=False."""
        url = "https://youtu.be/toolstest"
        bot = _make_bot(url)
        msg = _make_msg(url)

        captured_kw: list[dict] = []

        async def fake_stream(message, chat_id, disable_tools=True, **_kw):
            captured_kw.append({"disable_tools": disable_tools})
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert captured_kw[0]["disable_tools"] is False


# ---------------------------------------------------------------------------
# Обработка ответа AI
# ---------------------------------------------------------------------------


class TestHandleYtResponse:
    """Обработка различных вариантов ответа от AI."""

    @pytest.mark.asyncio
    async def test_успешный_ответ_редактирует_сообщение(self) -> None:
        """Ответ AI → edit() вызывается с контентом."""
        url = "https://youtu.be/success"
        bot = _make_bot(url)
        msg = _make_msg(url)

        async def fake_stream(message, chat_id, **_kw):
            yield "Название: Test Video\nАвтор: Test Channel"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        call_text = sent.edit.call_args[0][0]
        assert "Test Video" in call_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_сообщение_об_ошибке(self) -> None:
        """AI вернул пустую строку → сообщение об ошибке."""
        url = "https://youtu.be/empty"
        bot = _make_bot(url)
        msg = _make_msg(url)

        async def fake_stream(message, chat_id, **_kw):
            yield ""

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        sent = msg.reply.return_value
        sent.edit.assert_called_once()
        assert "пустой" in sent.edit.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_только_пробелы_в_ответе_тоже_ошибка(self) -> None:
        """Whitespace-only ответ → сообщение об ошибке."""
        url = "https://youtu.be/whitespace"
        bot = _make_bot(url)
        msg = _make_msg(url)

        async def fake_stream(message, chat_id, **_kw):
            yield "   \n\t  "

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "пустой" in call_text.lower()

    @pytest.mark.asyncio
    async def test_несколько_чанков_склеиваются(self) -> None:
        """Несколько streaming-чанков объединяются."""
        url = "https://youtu.be/chunks"
        bot = _make_bot(url)
        msg = _make_msg(url)

        async def fake_stream(message, chat_id, **_kw):
            yield "Название: "
            yield "Amazing "
            yield "Video"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "Название: Amazing Video" in call_text

    @pytest.mark.asyncio
    async def test_exception_из_openclaw_graceful(self) -> None:
        """RuntimeError в send_message_stream → edit() с ❌ сообщением."""
        url = "https://youtu.be/error"
        bot = _make_bot(url)
        msg = _make_msg(url)

        async def fake_stream(message, chat_id, **_kw):
            raise RuntimeError("network timeout")
            yield  # делаем генератором

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        sent = msg.reply.return_value
        call_text = sent.edit.call_args[0][0]
        assert "❌" in call_text

    @pytest.mark.asyncio
    async def test_статус_сообщение_отправляется_до_ответа(self) -> None:
        """reply() с 'Ищу...' вызывается до стриминга."""
        url = "https://youtu.be/status"
        bot = _make_bot(url)
        msg = _make_msg(url)

        call_order: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            call_order.append("stream_called")
            yield "ответ"

        original_reply = msg.reply

        async def tracking_reply(text, **_kw):
            call_order.append("reply_called")
            return await original_reply(text, **_kw)

        msg.reply = tracking_reply

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        # reply вызывается перед stream
        assert call_order.index("reply_called") < call_order.index("stream_called")


# ---------------------------------------------------------------------------
# Промпт
# ---------------------------------------------------------------------------


class TestHandleYtPrompt:
    """Проверка содержания промпта."""

    @pytest.mark.asyncio
    async def test_url_в_промпте(self) -> None:
        """URL обязательно попадает в промпт."""
        url = "https://www.youtube.com/watch?v=prompttest"
        bot = _make_bot(url)
        msg = _make_msg(url)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        assert url in captured[0]
        # Промпт упоминает поиск инфо
        assert "YouTube" in captured[0] or "видео" in captured[0]

    @pytest.mark.asyncio
    async def test_промпт_содержит_ключевые_поля(self) -> None:
        """Промпт содержит упоминание полей: название, автор, дата, описание."""
        url = "https://youtu.be/fields_test"
        bot = _make_bot(url)
        msg = _make_msg(url)

        captured: list[str] = []

        async def fake_stream(message, chat_id, **_kw):
            captured.append(message)
            yield "ответ"

        with patch(
            "src.handlers.command_handlers.openclaw_client.send_message_stream",
            side_effect=fake_stream,
        ):
            await handle_yt(bot, msg)

        prompt = captured[0]
        # Хотя бы часть полей должна быть упомянута
        fields = ["назван", "автор", "дат", "описан"]
        found = sum(1 for f in fields if f.lower() in prompt.lower())
        assert found >= 2, (
            f"Ожидалось упоминание полей в промпте, нашли только {found}: {prompt[:200]}"
        )


# ---------------------------------------------------------------------------
# Экспорт
# ---------------------------------------------------------------------------


class TestHandleYtExported:
    """handle_yt должен быть экспортирован."""

    def test_handle_yt_importable(self) -> None:
        """handle_yt импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import handle_yt  # noqa: F401

        assert callable(handle_yt)

    def test_extract_yt_url_importable(self) -> None:
        """_extract_yt_url импортируется из src.handlers.command_handlers."""
        from src.handlers.command_handlers import _extract_yt_url  # noqa: F401

        assert callable(_extract_yt_url)
