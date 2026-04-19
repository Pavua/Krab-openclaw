# -*- coding: utf-8 -*-
"""
Тесты улучшенного обработчика !search.

Покрытие:
  1. Пустой запрос → UserInputError с подсказкой
  2. Запрос "search" → UserInputError
  3. AI-режим (по умолчанию): вызывает openclaw_client.send_message_stream
  4. AI-режим: результат отображается с заголовком
  5. AI-режим: пустой ответ → ошибка
  6. AI-режим: пагинация при длинном ответе
  7. AI-режим: индикаторы части 1/N при множестве страниц
  8. AI-режим: без суффикса при 2 частях (только часть 1/2 в первом)
  9. AI-режим: исключение → сообщение об ошибке
  10. --raw режим: вызывает search_brave
  11. --brave режим: тот же результат что --raw
  12. --raw режим: пагинация длинного Brave-ответа
  13. --raw режим: пустой Brave-ответ → "Ничего не найдено"
  14. --raw режим: HTTP-ошибка → сообщение об ошибке
  15. --raw с пустым запросом → UserInputError
  16. AI-режим: изолированная сессия не смешивается с основным чатом
  17. AI-режим: disable_tools=False передаётся в send_message_stream
  18. AI-режим: промпт содержит исходный запрос
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import src.handlers.command_handlers as ch_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_search

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message(chat_id: int = 12345) -> SimpleNamespace:
    """Мок Telegram-сообщения с reply и edit."""
    sent = SimpleNamespace(edit=AsyncMock())
    msg = SimpleNamespace(
        reply=AsyncMock(return_value=sent),
        chat=SimpleNamespace(id=chat_id),
    )
    return msg, sent


def _make_async_gen(items: list[str]):
    """Создаёт async-генератор из списка строк."""

    async def _gen():
        for item in items:
            yield item

    return _gen()


# ---------------------------------------------------------------------------
# 1–2. Пустой запрос
# ---------------------------------------------------------------------------


class TestHandleSearchEmptyQuery:
    """Пустой / тривиальный запрос → UserInputError."""

    @pytest.mark.asyncio
    async def test_пустой_аргумент_бросает_userinputerror(self):
        bot = _make_bot("")
        msg, _ = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_search(bot, msg)
        assert "!search" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_аргумент_search_бросает_userinputerror(self):
        bot = _make_bot("search")
        msg, _ = _make_message()
        with pytest.raises(UserInputError):
            await handle_search(bot, msg)

    @pytest.mark.asyncio
    async def test_bang_search_бросает_userinputerror(self):
        bot = _make_bot("!search")
        msg, _ = _make_message()
        with pytest.raises(UserInputError):
            await handle_search(bot, msg)

    @pytest.mark.asyncio
    async def test_подсказка_содержит_raw_флаг(self):
        bot = _make_bot("")
        msg, _ = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_search(bot, msg)
        assert "--raw" in exc_info.value.user_message


# ---------------------------------------------------------------------------
# 3–9. AI-режим (по умолчанию)
# ---------------------------------------------------------------------------


class TestHandleSearchAIMode:
    """AI-режим: openclaw_client.send_message_stream."""

    @pytest.mark.asyncio
    async def test_вызывает_send_message_stream(self, monkeypatch: pytest.MonkeyPatch):
        """AI-режим вызывает openclaw_client.send_message_stream."""
        bot = _make_bot("python asyncio")
        msg, sent = _make_message()

        mock_stream = MagicMock()
        mock_stream.__aiter__ = MagicMock(return_value=iter(["ответ"]))

        async def fake_stream(**kwargs):
            yield "краткий ответ"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)
        sent.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_результат_содержит_запрос_в_заголовке(self, monkeypatch: pytest.MonkeyPatch):
        """Заголовок ответа включает исходный запрос."""
        bot = _make_bot("котики")
        msg, sent = _make_message()

        async def fake_stream(**kwargs):
            yield "Котики — пушистые животные."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        text = sent.edit.await_args.args[0]
        assert "котики" in text.lower()

    @pytest.mark.asyncio
    async def test_пустой_ответ_ai_показывает_ошибку(self, monkeypatch: pytest.MonkeyPatch):
        """Пустой chunk-поток → сообщение об ошибке."""
        bot = _make_bot("пустота")
        msg, sent = _make_message()

        async def fake_stream(**kwargs):
            # Пустой async-генератор — не выдаёт ни одного chunk
            if False:  # noqa: SIM210
                yield ""

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        text = sent.edit.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_disable_tools_false_передаётся(self, monkeypatch: pytest.MonkeyPatch):
        """disable_tools=False всегда передаётся в stream."""
        bot = _make_bot("тест инструмент")
        msg, sent = _make_message()

        captured_kwargs: dict = {}

        async def fake_stream(**kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        assert captured_kwargs.get("disable_tools") is False

    @pytest.mark.asyncio
    async def test_промпт_содержит_запрос(self, monkeypatch: pytest.MonkeyPatch):
        """Промпт к AI содержит исходный запрос пользователя."""
        bot = _make_bot("лучший python фреймворк")
        msg, sent = _make_message()

        captured_kwargs: dict = {}

        async def fake_stream(**kwargs):
            captured_kwargs.update(kwargs)
            yield "Django, FastAPI, Flask"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        assert "лучший python фреймворк" in captured_kwargs.get("message", "")

    @pytest.mark.asyncio
    async def test_изолированная_сессия_по_chat_id(self, monkeypatch: pytest.MonkeyPatch):
        """session_id изолирован: содержит chat.id, не равен просто chat.id."""
        bot = _make_bot("изоляция")
        msg, sent = _make_message(chat_id=99999)

        captured_kwargs: dict = {}

        async def fake_stream(**kwargs):
            captured_kwargs.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        chat_id_arg = captured_kwargs.get("chat_id", "")
        assert "99999" in str(chat_id_arg)
        # Должна быть изолированная сессия, не просто "99999"
        assert chat_id_arg != 99999

    @pytest.mark.asyncio
    async def test_исключение_в_stream_показывает_ошибку(self, monkeypatch: pytest.MonkeyPatch):
        """Исключение в send_message_stream → edit с ❌."""
        bot = _make_bot("сломанный запрос")
        msg, sent = _make_message()

        async def fake_stream(**kwargs):
            raise RuntimeError("OpenClaw недоступен")
            yield ""  # делает функцию async-генератором

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        text = sent.edit.await_args.args[0]
        assert "❌" in text

    @pytest.mark.asyncio
    async def test_один_chunk_без_пагинации(self, monkeypatch: pytest.MonkeyPatch):
        """Короткий ответ → только один edit, без reply."""
        bot = _make_bot("краткий запрос")
        msg, sent = _make_message()

        async def fake_stream(**kwargs):
            yield "Короткий ответ."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        # edit вызван один раз
        assert sent.edit.call_count == 1
        # reply не вызывался (только первоначальный reply для "Краб ищет...")
        assert msg.reply.call_count == 1

    @pytest.mark.asyncio
    async def test_пагинация_длинного_ответа(self, monkeypatch: pytest.MonkeyPatch):
        """Ответ >3900 символов → первый edit + reply для доп. частей."""
        bot = _make_bot("длинный запрос")
        msg, sent = _make_message()

        # Генерируем ответ > 4000 символов
        long_response = "Строка результата поиска.\n" * 200  # ~5200 символов

        async def fake_stream(**kwargs):
            yield long_response

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        # edit должен был вызваться с частью 1
        edit_text = sent.edit.await_args.args[0]
        assert "1/" in edit_text  # "часть 1/N"

        # reply должен вызываться > 1 раза (первый — "Краб ищет", далее — части)
        assert msg.reply.call_count >= 2

    @pytest.mark.asyncio
    async def test_первая_часть_имеет_суффикс_при_множестве_страниц(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """При total > 1 первый edit содержит '_(часть 1/N)_'."""
        bot = _make_bot("многостраничный")
        msg, sent = _make_message()

        long_response = "x" * 8000  # явно больше 3900

        async def fake_stream(**kwargs):
            yield long_response

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "часть 1/" in edit_text


# ---------------------------------------------------------------------------
# 10–14. --raw режим
# ---------------------------------------------------------------------------


class TestHandleSearchRawMode:
    """--raw: прямой Brave-поиск без AI."""

    @pytest.mark.asyncio
    async def test_raw_флаг_вызывает_search_brave(self, monkeypatch: pytest.MonkeyPatch):
        """--raw вызывает search_brave, не openclaw."""
        bot = _make_bot("--raw python новости")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module, "search_brave", AsyncMock(return_value="Новость 1\nНовость 2")
        )
        await handle_search(bot, msg)

        ch_module.search_brave.assert_called_once_with("python новости")

    @pytest.mark.asyncio
    async def test_brave_флаг_эквивалентен_raw(self, monkeypatch: pytest.MonkeyPatch):
        """--brave аналогичен --raw."""
        bot = _make_bot("--brave котики")
        msg, sent = _make_message()

        monkeypatch.setattr(ch_module, "search_brave", AsyncMock(return_value="Котики!"))
        await handle_search(bot, msg)

        ch_module.search_brave.assert_called_once_with("котики")

    @pytest.mark.asyncio
    async def test_raw_результат_отображается(self, monkeypatch: pytest.MonkeyPatch):
        """Результат Brave присутствует в ответе."""
        bot = _make_bot("--raw django rest")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module, "search_brave", AsyncMock(return_value="Django REST Framework")
        )
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "Django REST Framework" in edit_text

    @pytest.mark.asyncio
    async def test_raw_пустой_ответ_brave(self, monkeypatch: pytest.MonkeyPatch):
        """Пустой ответ Brave → 'Ничего не найдено'."""
        bot = _make_bot("--raw xyzzy abcdef")
        msg, sent = _make_message()

        monkeypatch.setattr(ch_module, "search_brave", AsyncMock(return_value=""))
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_raw_http_ошибка(self, monkeypatch: pytest.MonkeyPatch):
        """HTTP-ошибка при raw-поиске → сообщение об ошибке."""
        bot = _make_bot("--raw сеть сломана")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module, "search_brave", AsyncMock(side_effect=httpx.HTTPError("Connection timeout"))
        )
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_raw_os_error(self, monkeypatch: pytest.MonkeyPatch):
        """OSError при raw-поиске обрабатывается gracefully."""
        bot = _make_bot("--raw dns ошибка")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module, "search_brave", AsyncMock(side_effect=OSError("Name resolution failed"))
        )
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_raw_пустой_запрос_после_флага(self):
        """--raw без текста запроса → UserInputError."""
        bot = _make_bot("--raw")
        msg, _ = _make_message()
        with pytest.raises(UserInputError):
            await handle_search(bot, msg)

    @pytest.mark.asyncio
    async def test_raw_пагинация_длинного_brave_ответа(self, monkeypatch: pytest.MonkeyPatch):
        """Длинный Brave-ответ разбивается на части."""
        bot = _make_bot("--raw длинный результат")
        msg, sent = _make_message()

        long_results = "Результат поиска номер один.\n" * 200  # > 4000 символов
        monkeypatch.setattr(ch_module, "search_brave", AsyncMock(return_value=long_results))
        await handle_search(bot, msg)

        # Первый edit вызван
        assert sent.edit.call_count == 1
        # Дополнительные части отправлены через reply
        assert msg.reply.call_count >= 2

    @pytest.mark.asyncio
    async def test_raw_не_вызывает_openclaw(self, monkeypatch: pytest.MonkeyPatch):
        """В --raw режиме openclaw_client.send_message_stream НЕ вызывается."""
        bot = _make_bot("--raw изоляция ai")
        msg, sent = _make_message()

        mock_stream = AsyncMock()
        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", mock_stream)
        monkeypatch.setattr(ch_module, "search_brave", AsyncMock(return_value="результат"))

        await handle_search(bot, msg)

        mock_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_raw_заголовок_содержит_запрос(self, monkeypatch: pytest.MonkeyPatch):
        """В raw-режиме заголовок ответа содержит запрос."""
        bot = _make_bot("--raw fastapi docs")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module, "search_brave", AsyncMock(return_value="FastAPI документация")
        )
        await handle_search(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "fastapi docs" in edit_text.lower()


# ---------------------------------------------------------------------------
# Общие тесты: split_text_for_telegram интеграция
# ---------------------------------------------------------------------------


class TestSplitTextIntegration:
    """Проверяем, что handle_search использует _split_text_for_telegram правильно."""

    @pytest.mark.asyncio
    async def test_ровно_один_ответ_для_короткого_текста(self, monkeypatch: pytest.MonkeyPatch):
        """Короткий ответ → ровно 1 edit, ровно 1 reply (начальный)."""
        bot = _make_bot("краткость")
        msg, sent = _make_message()

        async def fake_stream(**kwargs):
            yield "Краткий ответ в одно сообщение."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        assert sent.edit.call_count == 1
        assert msg.reply.call_count == 1  # только начальное "Краб ищет..."

    @pytest.mark.asyncio
    async def test_многочастный_ответ_нумерован(self, monkeypatch: pytest.MonkeyPatch):
        """При нескольких частях первая часть содержит '1/' в суффиксе."""
        bot = _make_bot("нумерация частей")
        msg, sent = _make_message()

        # Ответ на 3 страницы (~12000 символов)
        long_answer = "Очень подробный ответ на один абзац.\n" * 400

        async def fake_stream(**kwargs):
            yield long_answer

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_search(bot, msg)

        first_part = sent.edit.await_args.args[0]
        assert "1/" in first_part
