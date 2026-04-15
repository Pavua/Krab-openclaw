# -*- coding: utf-8 -*-
"""
Тесты команды !rate — курсы криптовалют и акций.

Покрывает:
- Вспомогательные функции (_rate_asset_label, _build_rate_prompt)
- handle_rate: корректный сценарий (один актив, несколько активов)
- handle_rate: пустые аргументы → UserInputError
- handle_rate: обрезка до MAX_ASSETS
- handle_rate: пагинация длинного ответа
- handle_rate: ошибка stream → сообщение об ошибке
- handle_rate: session_id изолирован от основного чата
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _RATE_MAX_ASSETS,
    _build_rate_prompt,
    _rate_asset_label,
    handle_rate,
)

# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные stub-объекты
# ─────────────────────────────────────────────────────────────────────────────


def _make_bot(args: str = "") -> SimpleNamespace:
    """Минимальный bot-стаб с _get_command_args."""
    bot = SimpleNamespace()
    bot._get_command_args = lambda msg: args
    return bot


def _make_message(chat_id: int = 12345) -> SimpleNamespace:
    """Минимальный message-стаб."""
    sent_msg = SimpleNamespace(edit=AsyncMock())
    msg = SimpleNamespace(
        chat=SimpleNamespace(id=chat_id),
        reply=AsyncMock(return_value=sent_msg),
    )
    return msg, sent_msg


def _make_stream(*chunks: str):
    """Создаёт async-генератор из строк чанков."""
    async def _gen():
        for chunk in chunks:
            yield chunk
    return _gen()


# ─────────────────────────────────────────────────────────────────────────────
# Тесты _rate_asset_label
# ─────────────────────────────────────────────────────────────────────────────


class TestRateAssetLabel:
    """Тесты функции _rate_asset_label."""

    def test_btc_возвращает_читаемое_имя(self):
        assert _rate_asset_label("btc") == "Bitcoin (BTC)"

    def test_eth_возвращает_читаемое_имя(self):
        assert _rate_asset_label("eth") == "Ethereum (ETH)"

    def test_регистр_нечувствителен(self):
        assert _rate_asset_label("BTC") == "Bitcoin (BTC)"
        assert _rate_asset_label("ETH") == "Ethereum (ETH)"
        assert _rate_asset_label("Sol") == "Solana (SOL)"

    def test_неизвестный_тикер_возвращает_верхний_регистр(self):
        assert _rate_asset_label("AAPL") == "AAPL"
        assert _rate_asset_label("tsla") == "TSLA"
        assert _rate_asset_label("nvda") == "NVDA"

    def test_все_крипто_тикеры_в_словаре(self):
        """Все зарегистрированные крипто-тикеры возвращают имена с тикером в скобках."""
        crypto_tickers = ["btc", "eth", "sol", "bnb", "xrp", "ada", "doge", "ton", "usdt", "usdc", "avax", "link", "dot", "ltc", "shib"]
        for ticker in crypto_tickers:
            label = _rate_asset_label(ticker)
            assert "(" in label and ")" in label, f"{ticker} должен иметь читаемое имя"


# ─────────────────────────────────────────────────────────────────────────────
# Тесты _build_rate_prompt
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildRatePrompt:
    """Тесты функции _build_rate_prompt."""

    def test_один_актив_содержит_имя(self):
        prompt = _build_rate_prompt(["btc"])
        assert "Bitcoin (BTC)" in prompt

    def test_один_актив_запрашивает_цену_24h_капитализацию(self):
        prompt = _build_rate_prompt(["eth"])
        assert "цену" in prompt.lower() or "price" in prompt.lower()
        assert "24ч" in prompt or "24h" in prompt.lower()
        assert "капитализацию" in prompt.lower() or "капитализация" in prompt.lower()

    def test_один_актив_запрашивает_web_search(self):
        prompt = _build_rate_prompt(["btc"])
        assert "веб-поиска" in prompt or "web" in prompt.lower()

    def test_несколько_активов_содержат_все_имена(self):
        prompt = _build_rate_prompt(["btc", "eth"])
        assert "Bitcoin (BTC)" in prompt
        assert "Ethereum (ETH)" in prompt

    def test_несколько_активов_запрашивает_сравнение(self):
        prompt = _build_rate_prompt(["btc", "eth", "sol"])
        # Промпт для нескольких активов должен упоминать сравнение
        assert "сравнение" in prompt.lower() or "каждого" in prompt.lower()

    def test_акции_в_верхнем_регистре_в_промпте(self):
        prompt = _build_rate_prompt(["AAPL"])
        assert "AAPL" in prompt

    def test_один_vs_несколько_разные_промпты(self):
        """Одиночный актив и несколько активов генерируют разные промпты."""
        single = _build_rate_prompt(["btc"])
        multi = _build_rate_prompt(["btc", "eth"])
        assert single != multi


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — корректные сценарии
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_один_тикер_отправляет_ответ():
    """handle_rate с одним тикером — статус + редактирование с результатом."""
    bot = _make_bot("btc")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "Bitcoin цена: $65,000 (+2.3% за 24ч)"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    # Статус-сообщение отправлено
    msg.reply.assert_called_once()
    reply_text = msg.reply.call_args.args[0]
    assert "Bitcoin (BTC)" in reply_text

    # Результат вставлен через edit
    sent_msg.edit.assert_called_once()
    edit_text = sent_msg.edit.call_args.args[0]
    assert "$65,000" in edit_text


@pytest.mark.asyncio
async def test_handle_rate_eth_упоминает_ethereum():
    """handle_rate eth — в статус-сообщении есть Ethereum."""
    bot = _make_bot("eth")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "Ethereum: $3,200"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Ethereum (ETH)" in reply_text


@pytest.mark.asyncio
async def test_handle_rate_акция_apple():
    """handle_rate AAPL — в статус-сообщении AAPL в верхнем регистре."""
    bot = _make_bot("AAPL")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "Apple Inc. (AAPL): $178.50"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "AAPL" in reply_text


@pytest.mark.asyncio
async def test_handle_rate_несколько_тикеров():
    """handle_rate btc eth — оба тикера упоминаются в статус-сообщении."""
    bot = _make_bot("btc eth")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "BTC: $65k, ETH: $3.2k"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Bitcoin (BTC)" in reply_text
    assert "Ethereum (ETH)" in reply_text


@pytest.mark.asyncio
async def test_handle_rate_тикеры_через_запятую():
    """handle_rate btc,eth — запятая как разделитель работает корректно."""
    bot = _make_bot("btc,eth")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "BTC: $65k, ETH: $3.2k"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Bitcoin (BTC)" in reply_text
    assert "Ethereum (ETH)" in reply_text


@pytest.mark.asyncio
async def test_handle_rate_заголовок_в_ответе():
    """Ответ содержит заголовок с названием актива."""
    bot = _make_bot("sol")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "Solana: $140"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    edit_text = sent_msg.edit.call_args.args[0]
    # Заголовок должен содержать имя актива
    assert "Solana (SOL)" in edit_text


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — пустые аргументы
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_пустые_аргументы_raises_user_input_error():
    """Пустой !rate → UserInputError с подсказкой."""
    bot = _make_bot("")
    msg, _ = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_rate(bot, msg)

    error_msg = exc_info.value.user_message
    assert "!rate btc" in error_msg or "тикер" in error_msg.lower()


@pytest.mark.asyncio
async def test_handle_rate_только_пробелы_raises_user_input_error():
    """!rate с только пробелами → UserInputError."""
    bot = _make_bot("   ")
    msg, _ = _make_message()

    with pytest.raises(UserInputError):
        await handle_rate(bot, msg)


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — ограничение MAX_ASSETS
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_обрезка_до_max_assets():
    """Более MAX_ASSETS тикеров — обрезается до максимума без ошибок."""
    # Создаём строку с MAX_ASSETS+2 тикерами
    many_tickers = " ".join(["btc", "eth", "sol", "bnb", "xrp", "ada", "doge"])
    assert len(many_tickers.split()) > _RATE_MAX_ASSETS

    bot = _make_bot(many_tickers)
    msg, sent_msg = _make_message()

    captured_prompt: list[str] = []

    async def fake_stream(message: str, **kwargs):
        captured_prompt.append(message)
        yield "курс получен"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    # Промпт содержит не более MAX_ASSETS активов (проверяем через заголовок)
    reply_text = msg.reply.call_args.args[0]
    # Последний тикер (ada, doge — > MAX_ASSETS) не должен быть в заголовке
    all_labels_in_header = reply_text.count(",") + 1  # количество запятых + 1 ≈ количество активов
    assert all_labels_in_header <= _RATE_MAX_ASSETS


def test_rate_max_assets_константа():
    """_RATE_MAX_ASSETS должен быть положительным целым числом."""
    assert isinstance(_RATE_MAX_ASSETS, int)
    assert _RATE_MAX_ASSETS > 0


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — пагинация
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_короткий_ответ_без_пагинации():
    """Короткий ответ → одно edit, нет дополнительных reply."""
    bot = _make_bot("btc")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "BTC: $65,000"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    # reply вызван один раз (статус-сообщение)
    assert msg.reply.call_count == 1
    # edit вызван один раз (финальный ответ)
    assert sent_msg.edit.call_count == 1


@pytest.mark.asyncio
async def test_handle_rate_длинный_ответ_пагинация():
    """Очень длинный ответ → первая часть через edit, остальные через reply."""
    bot = _make_bot("btc")
    msg, sent_msg = _make_message()

    # Генерируем текст длиннее лимита (4096 символов)
    long_chunk = "A" * 4200

    async def fake_stream(*args, **kwargs):
        yield long_chunk

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    # Первый reply — статус, второй — вторая страница
    assert msg.reply.call_count >= 2
    # edit вызван (первая страница)
    assert sent_msg.edit.call_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — обработка ошибок
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_ошибка_stream_показывает_сообщение_об_ошибке():
    """Исключение в stream → пользователь получает сообщение об ошибке."""
    bot = _make_bot("btc")
    msg, sent_msg = _make_message()

    async def fake_stream_error(*args, **kwargs):
        raise RuntimeError("network timeout")
        yield  # нужен для синтаксиса async generator

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream_error,
    ):
        await handle_rate(bot, msg)

    # Ошибка показана пользователю через edit
    sent_msg.edit.assert_called_once()
    edit_text = sent_msg.edit.call_args.args[0]
    assert "❌" in edit_text
    assert "network timeout" in edit_text or "Ошибка" in edit_text


@pytest.mark.asyncio
async def test_handle_rate_пустой_ответ_stream():
    """Stream возвращает пустую строку → сообщение о неудаче."""
    bot = _make_bot("eth")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield ""  # пустой чанк

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    sent_msg.edit.assert_called_once()
    edit_text = sent_msg.edit.call_args.args[0]
    assert "❌" in edit_text


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — изоляция сессии
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_session_id_изолирован_от_чата():
    """session_id должен начинаться с 'rate_' и содержать chat_id."""
    chat_id = 99887766
    bot = _make_bot("btc")
    msg, sent_msg = _make_message(chat_id=chat_id)

    captured_kwargs: dict = {}

    async def fake_stream(message: str, chat_id: str, **kwargs):
        captured_kwargs["chat_id"] = chat_id
        yield "BTC: $65k"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    assert captured_kwargs["chat_id"] == f"rate_{chat_id}"


@pytest.mark.asyncio
async def test_handle_rate_разные_чаты_разные_сессии():
    """Два разных chat_id → два разных session_id."""
    sessions: list[str] = []

    async def fake_stream(message: str, chat_id: str, **kwargs):
        sessions.append(chat_id)
        yield "price"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        bot = _make_bot("btc")
        msg1, _ = _make_message(chat_id=111)
        await handle_rate(bot, msg1)

        msg2, _ = _make_message(chat_id=222)
        await handle_rate(bot, msg2)

    assert len(sessions) == 2
    assert sessions[0] != sessions[1]
    assert "111" in sessions[0]
    assert "222" in sessions[1]


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — disable_tools=False
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_disable_tools_false():
    """handle_rate всегда передаёт disable_tools=False для web_search."""
    bot = _make_bot("btc")
    msg, sent_msg = _make_message()

    captured_kwargs: dict = {}

    async def fake_stream(message: str, chat_id: str, disable_tools: bool = True, **kwargs):
        captured_kwargs["disable_tools"] = disable_tools
        yield "BTC price"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    assert captured_kwargs.get("disable_tools") is False


# ─────────────────────────────────────────────────────────────────────────────
# Тесты handle_rate — регистр тикеров
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_rate_тикер_в_нижнем_регистре_btc():
    """!rate BTC (верхний регистр) → корректный label Bitcoin (BTC)."""
    bot = _make_bot("BTC")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "BTC: $65k"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Bitcoin (BTC)" in reply_text


@pytest.mark.asyncio
async def test_handle_rate_тикер_смешанный_регистр():
    """!rate Btc (смешанный регистр) → распознаётся как Bitcoin."""
    bot = _make_bot("Btc")
    msg, sent_msg = _make_message()

    async def fake_stream(*args, **kwargs):
        yield "BTC price"

    with patch(
        "src.handlers.command_handlers.openclaw_client.send_message_stream",
        side_effect=fake_stream,
    ):
        await handle_rate(bot, msg)

    reply_text = msg.reply.call_args.args[0]
    assert "Bitcoin (BTC)" in reply_text
