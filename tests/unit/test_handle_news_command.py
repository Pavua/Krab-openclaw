# -*- coding: utf-8 -*-
"""
Тесты обработчика !news — быстрые новости через AI.

Покрытие:
  1.  !news (без аргументов) → запрашивает «мировые события»
  2.  !news <тема> → запрашивает конкретную тему
  3.  !news ru → добавляет «на русском языке» к промпту
  4.  !news en → добавляет «на английском языке» к промпту
  5.  !news ru <тема> → язык + тема
  6.  Успешный ответ отображается с заголовком
  7.  Пустой ответ AI → сообщение об ошибке
  8.  Исключение в send_message_stream → edit с ❌
  9.  disable_tools=False передаётся в send_message_stream
  10. Изолированная сессия: chat_id содержит «news_»
  11. Промпт содержит топ-5
  12. Промпт содержит запрошенную тему
  13. Короткий ответ → ровно 1 edit
  14. Длинный ответ → пагинация, первый edit содержит «1/»
  15. Индикатор загрузки отправляется до результата
  16. Заголовок ответа отображает тему
  17. !news рус → русскоязычный суффикс
  18. !news rus → русскоязычный суффикс (alias)
  19. Промпт содержит «источники» (требуем ссылки)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.handlers.command_handlers as ch_module
from src.handlers.command_handlers import handle_news

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message(chat_id: int = 42000) -> tuple[SimpleNamespace, SimpleNamespace]:
    """Мок Telegram-сообщения с reply и edit."""
    sent = SimpleNamespace(edit=AsyncMock())
    msg = SimpleNamespace(
        reply=AsyncMock(return_value=sent),
        chat=SimpleNamespace(id=chat_id),
    )
    return msg, sent


def _fake_stream_factory(response: str):
    """Возвращает async-генератор, выдающий одну строку."""

    async def _gen(**kwargs):
        yield response

    return _gen


def _empty_stream(**kwargs):
    """Пустой async-генератор — нет ни одного chunk."""
    if False:  # noqa: SIM210
        yield ""


async def _raising_stream(**kwargs):
    """Async-генератор, бросающий исключение."""
    raise RuntimeError("OpenClaw недоступен")
    yield ""  # делает функцию генератором


# ---------------------------------------------------------------------------
# 1. Без аргументов
# ---------------------------------------------------------------------------


class TestHandleNewsNoArgs:
    """!news без аргументов → тема 'мировые события'."""

    @pytest.mark.asyncio
    async def test_запрашивает_мировые_события(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "Новость 1. Новость 2."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "мировые события" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_индикатор_загрузки_отправляется(self, monkeypatch: pytest.MonkeyPatch):
        """reply вызван с индикатором до получения результата."""
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory("ok"),
        )
        await handle_news(bot, msg)

        # Первый reply — индикатор «Краб читает новости»
        first_reply_text = msg.reply.await_args_list[0].args[0]
        assert "📰" in first_reply_text


# ---------------------------------------------------------------------------
# 2. С темой
# ---------------------------------------------------------------------------


class TestHandleNewsWithTopic:
    """!news <тема> → промпт содержит тему."""

    @pytest.mark.asyncio
    async def test_тема_crypto_в_промпте(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("crypto")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "BTC упал на 5%."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "crypto" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_тема_ai_в_промпте(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("ai")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "GPT-5 вышел."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "ai" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_произвольная_тема_в_промпте(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("космос и астрофизика")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "Новая звезда."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "космос и астрофизика" in captured.get("message", "")


# ---------------------------------------------------------------------------
# 3–5. Языковые флаги
# ---------------------------------------------------------------------------


class TestHandleNewsLanguageFlags:
    """!news ru / !news en — добавляет языковой суффикс к промпту."""

    @pytest.mark.asyncio
    async def test_флаг_ru_добавляет_русский(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("ru")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "русском" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_флаг_рус_добавляет_русский(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("рус")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "русском" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_флаг_rus_добавляет_русский(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("rus")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "русском" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_флаг_en_добавляет_английский(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("en")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "английском" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_флаг_ru_с_темой(self, monkeypatch: pytest.MonkeyPatch):
        """!news ru tech → язык + тема в промпте."""
        bot = _make_bot("ru tech")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "Apple анонсировала M5."

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        prompt = captured.get("message", "")
        assert "русском" in prompt.lower()
        assert "tech" in prompt.lower()


# ---------------------------------------------------------------------------
# 6–8. Отображение результата и обработка ошибок
# ---------------------------------------------------------------------------


class TestHandleNewsResult:
    """Успешный ответ, пустой ответ, исключение."""

    @pytest.mark.asyncio
    async def test_результат_отображается(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory("1. Главная новость дня."),
        )
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "Главная новость дня" in edit_text

    @pytest.mark.asyncio
    async def test_пустой_ответ_показывает_ошибку(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", _empty_stream)
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_исключение_показывает_ошибку(self, monkeypatch: pytest.MonkeyPatch):
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", _raising_stream)
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "❌" in edit_text

    @pytest.mark.asyncio
    async def test_заголовок_содержит_тему(self, monkeypatch: pytest.MonkeyPatch):
        """Первый edit содержит тему в заголовке."""
        bot = _make_bot("crypto")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory("BTC: $100k"),
        )
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "crypto" in edit_text.lower()

    @pytest.mark.asyncio
    async def test_заголовок_содержит_эмодзи_газеты(self, monkeypatch: pytest.MonkeyPatch):
        """Ответ содержит 📰."""
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory("Новость."),
        )
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "📰" in edit_text


# ---------------------------------------------------------------------------
# 9–10. Параметры send_message_stream
# ---------------------------------------------------------------------------


class TestHandleNewsStreamParams:
    """Проверяем параметры, передаваемые в send_message_stream."""

    @pytest.mark.asyncio
    async def test_disable_tools_false(self, monkeypatch: pytest.MonkeyPatch):
        """disable_tools=False — web_search должен работать."""
        bot = _make_bot("")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert captured.get("disable_tools") is False

    @pytest.mark.asyncio
    async def test_изолированная_сессия_news_prefix(self, monkeypatch: pytest.MonkeyPatch):
        """chat_id для OpenClaw начинается с 'news_'."""
        bot = _make_bot("")
        msg, sent = _make_message(chat_id=77777)

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        session = str(captured.get("chat_id", ""))
        assert session.startswith("news_")

    @pytest.mark.asyncio
    async def test_изолированная_сессия_содержит_chat_id(self, monkeypatch: pytest.MonkeyPatch):
        """chat_id для OpenClaw содержит числовой id чата."""
        bot = _make_bot("")
        msg, sent = _make_message(chat_id=12345)

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        session = str(captured.get("chat_id", ""))
        assert "12345" in session

    @pytest.mark.asyncio
    async def test_промпт_содержит_топ5(self, monkeypatch: pytest.MonkeyPatch):
        """Промпт явно запрашивает топ-5."""
        bot = _make_bot("")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "топ-5" in captured.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_промпт_содержит_источники(self, monkeypatch: pytest.MonkeyPatch):
        """Промпт запрашивает источники/ссылки."""
        bot = _make_bot("")
        msg, sent = _make_message()

        captured: dict = {}

        async def fake_stream(**kwargs):
            captured.update(kwargs)
            yield "ok"

        monkeypatch.setattr(ch_module.openclaw_client, "send_message_stream", fake_stream)
        await handle_news(bot, msg)

        assert "источник" in captured.get("message", "").lower()


# ---------------------------------------------------------------------------
# 11–12. Пагинация
# ---------------------------------------------------------------------------


class TestHandleNewsPagination:
    """Пагинация длинных ответов."""

    @pytest.mark.asyncio
    async def test_короткий_ответ_один_edit(self, monkeypatch: pytest.MonkeyPatch):
        """Короткий ответ → ровно 1 edit, 1 reply (индикатор)."""
        bot = _make_bot("")
        msg, sent = _make_message()

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory("Краткие новости дня."),
        )
        await handle_news(bot, msg)

        assert sent.edit.call_count == 1
        assert msg.reply.call_count == 1  # только индикатор

    @pytest.mark.asyncio
    async def test_длинный_ответ_пагинация(self, monkeypatch: pytest.MonkeyPatch):
        """Длинный ответ → первый edit содержит '1/'."""
        bot = _make_bot("")
        msg, sent = _make_message()

        long_news = "Новость очень длинная. " * 300  # >4000 символов

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory(long_news),
        )
        await handle_news(bot, msg)

        edit_text = sent.edit.await_args.args[0]
        assert "1/" in edit_text

    @pytest.mark.asyncio
    async def test_длинный_ответ_дополнительные_reply(self, monkeypatch: pytest.MonkeyPatch):
        """При пагинации > 1 части reply вызывается более 1 раза."""
        bot = _make_bot("")
        msg, sent = _make_message()

        long_news = "x" * 9000  # явно больше 4096

        monkeypatch.setattr(
            ch_module.openclaw_client,
            "send_message_stream",
            _fake_stream_factory(long_news),
        )
        await handle_news(bot, msg)

        # Как минимум: 1 reply для индикатора + 1 reply для доп. части
        assert msg.reply.call_count >= 2
