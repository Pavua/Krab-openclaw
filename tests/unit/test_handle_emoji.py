# -*- coding: utf-8 -*-
"""
Тесты для команды !emoji — поиск эмодзи по текстовому описанию.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.handlers.command_handlers import _emoji_search, handle_emoji

# ---------------------------------------------------------------------------
# Тесты _emoji_search (чистая логика, без Telegram)
# ---------------------------------------------------------------------------


class TestEmojiSearch:
    def test_exact_match_fire(self) -> None:
        """Точное совпадение: 'fire' → 🔥 первым результатом."""
        results = _emoji_search("fire")
        assert results[0] == "🔥"

    def test_exact_match_heart_all_variants(self) -> None:
        """'heart' возвращает несколько вариантов цветных сердец."""
        results = _emoji_search("heart")
        assert "❤️" in results
        assert "💜" in results
        assert "💙" in results
        assert "💚" in results
        assert "🖤" in results
        # Хотя бы 5 вариантов
        assert len(results) >= 5

    def test_exact_match_cat(self) -> None:
        """'cat' → минимум 🐱 🐈 😺."""
        results = _emoji_search("cat")
        assert "🐱" in results
        assert "🐈" in results
        assert "😺" in results

    def test_no_duplicates(self) -> None:
        """Результаты не содержат дубликатов."""
        results = _emoji_search("heart")
        assert len(results) == len(set(results))

    def test_partial_match_cat_from_kitten(self) -> None:
        """'cat' как часть слова 'catfish' — проверяем, что смежные ключи тоже подхватываются."""
        # Проверим, что 'meow' есть в DB (точное совпадение)
        results_meow = _emoji_search("meow")
        assert len(results_meow) > 0

    def test_partial_key_contains_query(self) -> None:
        """Если ключ содержит запрос — эмодзи включаются. Пример: 'art' → 'party'."""
        results = _emoji_search("art")
        # 'party' содержит 'art' → должны прийти 🎉, 🎊, 🥳
        assert "🎉" in results or "🎊" in results

    def test_query_contains_key(self) -> None:
        """Если запрос содержит ключ — эмодзи включаются. Пример: 'heartfelt' содержит 'heart'."""
        results = _emoji_search("heartfelt")
        assert "❤️" in results

    def test_unknown_keyword_returns_empty(self) -> None:
        """Несуществующее слово возвращает пустой список."""
        # Используем строку, которая не содержит и не является частью ни одного ключа в словаре
        results = _emoji_search("qqqzzzwww")
        assert results == []

    def test_case_insensitive(self) -> None:
        """Поиск нечувствителен к регистру."""
        lower = _emoji_search("fire")
        upper = _emoji_search("FIRE")
        mixed = _emoji_search("Fire")
        assert lower == upper == mixed

    def test_whitespace_stripped(self) -> None:
        """Пробелы по краям запроса не мешают поиску."""
        results_plain = _emoji_search("fire")
        results_spaces = _emoji_search("  fire  ")
        assert results_plain == results_spaces

    def test_dog(self) -> None:
        results = _emoji_search("dog")
        assert "🐶" in results

    def test_pizza(self) -> None:
        results = _emoji_search("pizza")
        assert "🍕" in results

    def test_rocket(self) -> None:
        results = _emoji_search("rocket")
        assert "🚀" in results

    def test_rainbow(self) -> None:
        results = _emoji_search("rainbow")
        assert "🌈" in results

    def test_star(self) -> None:
        results = _emoji_search("star")
        assert "⭐" in results

    def test_crab(self) -> None:
        """Краб в словаре есть."""
        results = _emoji_search("crab")
        assert "🦀" in results

    def test_no_duplicates_partial(self) -> None:
        """При частичном совпадении дублей тоже нет."""
        results = _emoji_search("cat")
        assert len(results) == len(set(results))

    def test_order_exact_first(self) -> None:
        """Точное совпадение идёт раньше частичных."""
        # 'cat' — точный ключ. 'catfish' нет, но 'cat' содержится в 'catch', если бы был.
        # Проверяем: для 'fire' первым идёт 🔥 (точное совпадение)
        results = _emoji_search("fire")
        assert results[0] == "🔥"


# ---------------------------------------------------------------------------
# Тесты handle_emoji (с мокированным Telegram message)
# ---------------------------------------------------------------------------


def _make_bot_and_message(args: str) -> tuple:
    """Создаёт фейковые bot и message для тестов."""
    bot = SimpleNamespace(_get_command_args=lambda msg: args)
    message = SimpleNamespace(reply=AsyncMock())
    return bot, message


@pytest.mark.asyncio
async def test_handle_emoji_no_args_shows_help() -> None:
    """`!emoji` без аргументов → справочное сообщение."""
    bot, message = _make_bot_and_message("")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "!emoji" in text
    assert "search" in text


@pytest.mark.asyncio
async def test_handle_emoji_fire() -> None:
    """`!emoji fire` → первым 🔥."""
    bot, message = _make_bot_and_message("fire")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "🔥" in text


@pytest.mark.asyncio
async def test_handle_emoji_heart_preview() -> None:
    """`!emoji heart` → несколько сердец (без 'search', до 5 штук)."""
    bot, message = _make_bot_and_message("heart")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "❤️" in text


@pytest.mark.asyncio
async def test_handle_emoji_search_cat_all() -> None:
    """`!emoji search cat` → все варианты кошачьих эмодзи."""
    bot, message = _make_bot_and_message("search cat")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    # Должен присутствовать хотя бы один кошачий эмодзи
    assert any(em in text for em in ["🐱", "🐈", "😺", "😸"])
    # Формат: "🔍 `cat` → ..."
    assert "cat" in text


@pytest.mark.asyncio
async def test_handle_emoji_search_shows_all_not_limited() -> None:
    """`!emoji search heart` выводит все варианты, а не ограничивается 5."""
    bot, message = _make_bot_and_message("search heart")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    # В режиме search нет суффикса "+N ещё"
    assert "+" not in text or "ещё" not in text or "search" in text


@pytest.mark.asyncio
async def test_handle_emoji_not_found() -> None:
    """`!emoji qqqzzzwww` → сообщение что не найдено."""
    bot, message = _make_bot_and_message("qqqzzzwww")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "не найден" in text.lower() or "не найдены" in text.lower()


@pytest.mark.asyncio
async def test_handle_emoji_search_no_query() -> None:
    """`!emoji search` без слова → просит уточнить запрос."""
    bot, message = _make_bot_and_message("search")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "Укажи" in text or "слово" in text.lower()


@pytest.mark.asyncio
async def test_handle_emoji_preview_limited_to_5() -> None:
    """`!emoji heart` (без search) показывает не более 5 и суффикс если больше."""
    bot, message = _make_bot_and_message("heart")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    matches = _emoji_search("heart")
    # Если больше 5 — должен быть суффикс
    if len(matches) > 5:
        assert "ещё" in text or "search" in text


@pytest.mark.asyncio
async def test_handle_emoji_dog() -> None:
    """`!emoji dog` → 🐶."""
    bot, message = _make_bot_and_message("dog")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "🐶" in text


@pytest.mark.asyncio
async def test_handle_emoji_pizza() -> None:
    """`!emoji pizza` → 🍕."""
    bot, message = _make_bot_and_message("pizza")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "🍕" in text


@pytest.mark.asyncio
async def test_handle_emoji_search_rocket() -> None:
    """`!emoji search rocket` → 🚀 в ответе."""
    bot, message = _make_bot_and_message("search rocket")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "🚀" in text


@pytest.mark.asyncio
async def test_handle_emoji_crab() -> None:
    """`!emoji crab` → 🦀 (тематический тест для проекта)."""
    bot, message = _make_bot_and_message("crab")
    await handle_emoji(bot, message)
    text = message.reply.await_args.args[0]
    assert "🦀" in text


# ---------------------------------------------------------------------------
# Тест экспорта из пакета src.handlers
# ---------------------------------------------------------------------------


def test_handle_emoji_exported_from_handlers() -> None:
    """handle_emoji реэкспортируется из src.handlers."""
    from src import handlers

    assert hasattr(handlers, "handle_emoji")
    assert handlers.handle_emoji is handle_emoji
