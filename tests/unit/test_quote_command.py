# -*- coding: utf-8 -*-
"""
Тесты обработчика !quote — случайные и сохранённые цитаты.
"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.handlers.command_handlers import (
    _BUILTIN_QUOTES,
    _load_saved_quotes,
    _save_quotes,
    handle_quote,
)


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(args: str = "") -> SimpleNamespace:
    """Мок бота с _get_command_args."""
    return SimpleNamespace(_get_command_args=lambda _msg: args)


def _make_message(reply_to: object = None) -> SimpleNamespace:
    """Мок сообщения с AsyncMock reply."""
    return SimpleNamespace(
        reply=AsyncMock(),
        reply_to_message=reply_to,
    )


def _make_sender(first_name: str = "Иван", last_name: str = "", username: str = "") -> SimpleNamespace:
    return SimpleNamespace(first_name=first_name, last_name=last_name, username=username)


def _make_reply_message(text: str = "Тестовая цитата", sender: object = None) -> SimpleNamespace:
    """Мок reply-сообщения."""
    return SimpleNamespace(
        text=text,
        caption=None,
        from_user=sender or _make_sender(),
    )


# ---------------------------------------------------------------------------
# Тесты встроенного набора цитат
# ---------------------------------------------------------------------------


class TestBuiltinQuotes:
    """Проверки статичного списка встроенных цитат."""

    def test_количество_цитат_не_менее_50(self):
        assert len(_BUILTIN_QUOTES) >= 50

    def test_все_цитаты_непустые(self):
        for q in _BUILTIN_QUOTES:
            assert q.strip(), f"Пустая цитата: {q!r}"

    def test_есть_русскоязычные_цитаты(self):
        # Хотя бы одна содержит кириллицу
        has_ru = any(any("\u0400" <= c <= "\u04ff" for c in q) for q in _BUILTIN_QUOTES)
        assert has_ru

    def test_есть_англоязычные_цитаты(self):
        # Хотя бы одна начинается с латинской буквы (английская цитата)
        has_en = any(q and q[0].isascii() and q[0].isalpha() for q in _BUILTIN_QUOTES)
        assert has_en


# ---------------------------------------------------------------------------
# !quote (без аргументов) — случайная встроенная цитата
# ---------------------------------------------------------------------------


class TestQuoteNoArgs:
    """!quote без аргументов возвращает случайную встроенную цитату."""

    @pytest.mark.asyncio
    async def test_ответ_содержит_эмодзи_и_текст(self):
        bot = _make_bot("")
        msg = _make_message()
        await handle_quote(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "💬" in text

    @pytest.mark.asyncio
    async def test_ответ_содержит_цитату_из_набора(self):
        bot = _make_bot("")
        msg = _make_message()
        await handle_quote(bot, msg)
        answer = msg.reply.await_args.args[0]
        # Один из встроенных текстов должен присутствовать в ответе
        assert any(q in answer for q in _BUILTIN_QUOTES)

    @pytest.mark.asyncio
    async def test_несколько_вызовов_дают_разные_цитаты(self):
        """10 вызовов должны вернуть хотя бы 2 разных результата."""
        results: set[str] = set()
        for _ in range(10):
            bot = _make_bot("")
            msg = _make_message()
            await handle_quote(bot, msg)
            results.add(msg.reply.await_args.args[0])
        assert len(results) > 1

    @pytest.mark.asyncio
    async def test_reply_вызывается_ровно_один_раз(self):
        bot = _make_bot("")
        msg = _make_message()
        await handle_quote(bot, msg)
        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# !quote save — сохранение цитаты из reply
# ---------------------------------------------------------------------------


class TestQuoteSave:
    """!quote save сохраняет цитату из reply-сообщения."""

    @pytest.mark.asyncio
    async def test_без_reply_сообщение_об_ошибке(self):
        bot = _make_bot("save")
        msg = _make_message(reply_to=None)
        await handle_quote(bot, msg)
        text = msg.reply.await_args.args[0]
        assert "Ответь" in text or "reply" in text.lower() or "reply" in text

    @pytest.mark.asyncio
    async def test_пустое_сообщение_возвращает_ошибку(self):
        sender = _make_sender()
        reply = SimpleNamespace(text="", caption=None, from_user=sender)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)
        await handle_quote(bot, msg)
        answer = msg.reply.await_args.args[0]
        assert "не содержит текста" in answer

    @pytest.mark.asyncio
    async def test_сохранение_с_именем_автора(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        sender = _make_sender(first_name="Мария", last_name="Иванова")
        reply = _make_reply_message(text="Тест цитата", sender=sender)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "✅" in answer
        assert "Тест цитата" in answer
        assert "Мария" in answer

    @pytest.mark.asyncio
    async def test_сохранение_записывает_в_файл(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        sender = _make_sender(first_name="Тест")
        reply = _make_reply_message(text="Золотая мысль", sender=sender)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)
            data = json.loads(quotes_file.read_text(encoding="utf-8"))

        assert len(data) == 1
        assert data[0]["text"] == "Золотая мысль"
        assert data[0]["author"] == "Тест"

    @pytest.mark.asyncio
    async def test_несколько_сохранений_накапливаются(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        for i in range(3):
            sender = _make_sender(first_name=f"Автор{i}")
            reply = _make_reply_message(text=f"Цитата {i}", sender=sender)
            bot = _make_bot("save")
            msg = _make_message(reply_to=reply)
            with patch(
                "src.handlers.command_handlers._SAVED_QUOTES_PATH",
                quotes_file,
            ):
                await handle_quote(bot, msg)

        data = json.loads(quotes_file.read_text(encoding="utf-8"))
        assert len(data) == 3

    @pytest.mark.asyncio
    async def test_автор_только_username_если_нет_имени(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        sender = _make_sender(first_name="", last_name="", username="testuser")
        reply = _make_reply_message(text="Цитата без имени", sender=sender)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        data = json.loads(quotes_file.read_text(encoding="utf-8"))
        assert "@testuser" == data[0]["author"]

    @pytest.mark.asyncio
    async def test_автор_неизвестно_если_нет_sender(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        reply = SimpleNamespace(text="Аноним говорит", caption=None, from_user=None)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        data = json.loads(quotes_file.read_text(encoding="utf-8"))
        assert data[0]["author"] == "Неизвестно"

    @pytest.mark.asyncio
    async def test_ответ_содержит_номер_цитаты(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        sender = _make_sender()
        reply = _make_reply_message(text="Проверка номера")
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "#1" in answer

    @pytest.mark.asyncio
    async def test_caption_используется_если_нет_text(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        sender = _make_sender()
        reply = SimpleNamespace(text=None, caption="Подпись к фото", from_user=sender)
        bot = _make_bot("save")
        msg = _make_message(reply_to=reply)

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        data = json.loads(quotes_file.read_text(encoding="utf-8"))
        assert data[0]["text"] == "Подпись к фото"


# ---------------------------------------------------------------------------
# !quote my — случайная из сохранённых
# ---------------------------------------------------------------------------


class TestQuoteMy:
    """!quote my показывает случайную сохранённую цитату."""

    @pytest.mark.asyncio
    async def test_пустой_список_сообщение_об_отсутствии(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        bot = _make_bot("my")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "📭" in answer

    @pytest.mark.asyncio
    async def test_возвращает_цитату_из_файла(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        quotes_file.write_text(
            json.dumps([{"text": "Звёздная цитата", "author": "Автор"}], ensure_ascii=False),
            encoding="utf-8",
        )
        bot = _make_bot("my")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "Звёздная цитата" in answer
        assert "Автор" in answer

    @pytest.mark.asyncio
    async def test_ответ_содержит_эмодзи(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        quotes_file.write_text(
            json.dumps([{"text": "Тест", "author": "Кто-то"}], ensure_ascii=False),
            encoding="utf-8",
        )
        bot = _make_bot("my")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "💬" in answer

    @pytest.mark.asyncio
    async def test_много_цитат_разные_результаты(self, tmp_path):
        """При 10+ сохранённых цитатах повторные вызовы дают разные результаты."""
        quotes_file = tmp_path / "saved_quotes.json"
        many = [{"text": f"Цитата {i}", "author": "Авт"} for i in range(20)]
        quotes_file.write_text(json.dumps(many, ensure_ascii=False), encoding="utf-8")

        results: set[str] = set()
        for _ in range(15):
            bot = _make_bot("my")
            msg = _make_message()
            with patch(
                "src.handlers.command_handlers._SAVED_QUOTES_PATH",
                quotes_file,
            ):
                await handle_quote(bot, msg)
            results.add(msg.reply.await_args.args[0])

        assert len(results) > 1


# ---------------------------------------------------------------------------
# !quote list — список всех сохранённых
# ---------------------------------------------------------------------------


class TestQuoteList:
    """!quote list показывает все сохранённые цитаты."""

    @pytest.mark.asyncio
    async def test_пустой_список_сообщение_об_отсутствии(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        bot = _make_bot("list")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "📭" in answer

    @pytest.mark.asyncio
    async def test_показывает_все_цитаты(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        quotes_file.write_text(
            json.dumps(
                [
                    {"text": "Первая", "author": "А1"},
                    {"text": "Вторая", "author": "А2"},
                    {"text": "Третья", "author": "А3"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        bot = _make_bot("list")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "Первая" in answer
        assert "Вторая" in answer
        assert "Третья" in answer

    @pytest.mark.asyncio
    async def test_нумерация_в_списке(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        quotes_file.write_text(
            json.dumps(
                [{"text": "Цитата", "author": "Авт"}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        bot = _make_bot("list")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "1." in answer

    @pytest.mark.asyncio
    async def test_заголовок_списка_присутствует(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        quotes_file.write_text(
            json.dumps([{"text": "Тест", "author": "Авт"}], ensure_ascii=False),
            encoding="utf-8",
        )
        bot = _make_bot("list")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        assert "📚" in answer

    @pytest.mark.asyncio
    async def test_длинный_текст_обрезается(self, tmp_path):
        quotes_file = tmp_path / "saved_quotes.json"
        long_text = "А" * 200
        quotes_file.write_text(
            json.dumps([{"text": long_text, "author": "Авт"}], ensure_ascii=False),
            encoding="utf-8",
        )
        bot = _make_bot("list")
        msg = _make_message()

        with patch(
            "src.handlers.command_handlers._SAVED_QUOTES_PATH",
            quotes_file,
        ):
            await handle_quote(bot, msg)

        answer = msg.reply.await_args.args[0]
        # Полный текст 200 символов не должен присутствовать
        assert long_text not in answer
        assert "…" in answer


# ---------------------------------------------------------------------------
# !quote <неизвестная команда> — справка
# ---------------------------------------------------------------------------


class TestQuoteUnknownSubcommand:
    """!quote <что угодно другое> → справка."""

    @pytest.mark.asyncio
    async def test_неизвестная_команда_показывает_справку(self):
        bot = _make_bot("foobar")
        msg = _make_message()
        await handle_quote(bot, msg)
        answer = msg.reply.await_args.args[0]
        assert "!quote" in answer
        assert "save" in answer
        assert "list" in answer

    @pytest.mark.asyncio
    async def test_help_показывает_справку(self):
        bot = _make_bot("help")
        msg = _make_message()
        await handle_quote(bot, msg)
        answer = msg.reply.await_args.args[0]
        assert "!quote" in answer


# ---------------------------------------------------------------------------
# Тесты вспомогательных функций _load/_save
# ---------------------------------------------------------------------------


class TestLoadSaveQuotes:
    """Юнит-тесты _load_saved_quotes и _save_quotes."""

    def test_load_несуществующий_файл_возвращает_пустой_список(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", missing):
            result = _load_saved_quotes()
        assert result == []

    def test_load_повреждённый_json_возвращает_пустой_список(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json!!!", encoding="utf-8")
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", bad_file):
            result = _load_saved_quotes()
        assert result == []

    def test_load_некорректная_структура_возвращает_пустой_список(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text('{"key": "value"}', encoding="utf-8")
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", bad_file):
            result = _load_saved_quotes()
        assert result == []

    def test_save_создаёт_файл(self, tmp_path):
        quotes_file = tmp_path / "q.json"
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", quotes_file):
            _save_quotes([{"text": "Цитата", "author": "Автор", "saved_at": "2026-01-01T00:00:00"}])
        assert quotes_file.exists()

    def test_save_и_load_roundtrip(self, tmp_path):
        quotes_file = tmp_path / "q.json"
        original = [{"text": "Круговорот цитат", "author": "Я", "saved_at": "2026-01-01T00:00:00"}]
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", quotes_file):
            _save_quotes(original)
            loaded = _load_saved_quotes()
        assert loaded == original

    def test_save_unicode_сохраняется_корректно(self, tmp_path):
        quotes_file = tmp_path / "q.json"
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", quotes_file):
            _save_quotes([{"text": "Привет мир 世界", "author": "Тест", "saved_at": "2026-01-01T00:00:00"}])
        raw = quotes_file.read_text(encoding="utf-8")
        assert "Привет мир" in raw

    def test_save_создаёт_родительскую_директорию(self, tmp_path):
        quotes_file = tmp_path / "subdir" / "q.json"
        with patch("src.handlers.command_handlers._SAVED_QUOTES_PATH", quotes_file):
            _save_quotes([])
        assert quotes_file.parent.exists()
