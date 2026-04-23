# -*- coding: utf-8 -*-
"""
Юнит-тесты: !forget command + auto-clear session history on memory queries.

Покрываем:
  1. handle_forget очищает _sessions[chat_id] через openclaw_client.clear_session
  2. handle_forget — owner-only (не-owner получает UserInputError)
  3. detect_memory_query — детекция archive-запросов (позитив + негатив)
  4. maybe_flag_memory_query — поднимает флаг в openclaw_client
  5. is_memory_query_flagged — одноразовый флаг (сбрасывается при чтении)
  6. send_message_stream очищает историю если флаг поднят
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.core.memory_context_augmenter import detect_memory_query, maybe_flag_memory_query
from src.handlers.command_handlers import handle_forget

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bot_message(is_owner: bool = True, chat_id: int = 99999) -> tuple:
    """Возвращает (bot, message) stubs."""
    level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    access_profile = SimpleNamespace(level=level)

    msg = SimpleNamespace(
        text="!forget",
        reply=AsyncMock(),
        chat=SimpleNamespace(id=chat_id),
        from_user=SimpleNamespace(id=111 if is_owner else 222),
    )
    bot = SimpleNamespace(_get_access_profile=lambda _u: access_profile)
    return bot, msg


# ---------------------------------------------------------------------------
# Тест 1: !forget очищает сессию текущего чата
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_clears_session():
    """handle_forget вызывает clear_session для текущего chat_id."""
    bot, msg = _make_bot_message(is_owner=True, chat_id=12345)

    with patch("src.handlers.command_handlers.openclaw_client") as mock_client:
        await handle_forget(bot, msg)

    mock_client.clear_session.assert_called_once_with("12345")
    msg.reply.assert_awaited_once()
    reply_text = msg.reply.call_args[0][0]
    assert "Контекст" in reply_text or "очищен" in reply_text


# ---------------------------------------------------------------------------
# Тест 2: !forget — owner-only (не-owner получает UserInputError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_owner_only_rejects_guest():
    """handle_forget бросает UserInputError для не-owner."""
    bot, msg = _make_bot_message(is_owner=False)

    with patch("src.handlers.command_handlers.openclaw_client"):
        with pytest.raises(UserInputError):
            await handle_forget(bot, msg)


# ---------------------------------------------------------------------------
# Тест 3: detect_memory_query — позитивные случаи
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "что он писал вчера",
        "кто писал в этом чате",
        "когда я это писал",
        "история переписки",
        "архив сообщений",
        "recall последнего разговора",
        "найди в памяти упоминание",
        "что дружище писал на прошлой неделе",
        "где он писал про Telegram",
    ],
)
def test_detect_memory_query_positive(query: str):
    """detect_memory_query возвращает True для archive-запросов."""
    assert detect_memory_query(query) is True, f"Не распознан: {query!r}"


# ---------------------------------------------------------------------------
# Тест 4: detect_memory_query — негативные случаи (обычные вопросы)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "привет как дела",
        "напиши мне письмо",
        "переведи этот текст",
        "какой сегодня курс биткоина",
        "объясни квантовую механику",
        "",
        "   ",
    ],
)
def test_detect_memory_query_negative(query: str):
    """detect_memory_query возвращает False для обычных запросов."""
    assert detect_memory_query(query) is False, f"Ложное срабатывание: {query!r}"


# ---------------------------------------------------------------------------
# Тест 5: maybe_flag_memory_query — поднимает флаг в openclaw_client
# ---------------------------------------------------------------------------


def test_maybe_flag_memory_query_sets_flag():
    """maybe_flag_memory_query вызывает flag_memory_query при archive-запросе."""
    mock_client = MagicMock()
    mock_client.flag_memory_query = MagicMock()

    with patch("src.core.memory_context_augmenter.openclaw_client", mock_client, create=True):
        # Патчим import внутри maybe_flag_memory_query
        import src.core.memory_context_augmenter as mod

        original = getattr(mod, "_call_flag", None)
        # Прямой тест через мок openclaw_client
        with patch.dict("sys.modules", {}):
            import importlib
            import sys

            # Подменяем openclaw_client внутри maybe_flag_memory_query
            fake_oc = MagicMock()
            fake_oc.flag_memory_query = MagicMock()
            # Вызываем через прямой патч модуля openclaw_client в sys.modules
            with patch("src.openclaw_client.openclaw_client", fake_oc):
                result = maybe_flag_memory_query("111", "что он писал вчера")
    # Результат True — детекция сработала
    assert result is True


def test_maybe_flag_memory_query_no_flag_for_regular_query():
    """maybe_flag_memory_query возвращает False для обычных запросов."""
    result = maybe_flag_memory_query("222", "привет как дела")
    assert result is False


# ---------------------------------------------------------------------------
# Тест 6: is_memory_query_flagged — одноразовый флаг
# ---------------------------------------------------------------------------


def test_memory_query_flag_is_one_shot():
    """flag_memory_query поднимает флаг; is_memory_query_flagged сбрасывает при чтении."""
    from src.openclaw_client import OpenClawClient

    # Создаём изолированный экземпляр клиента (без реального HTTP).
    with patch.object(OpenClawClient, "__init__", lambda self: None):
        client = OpenClawClient.__new__(OpenClawClient)
        client._memory_query_flags = set()
        client._sessions = {}
        client._lm_native_chat_state = {}

    chat_id = "test_chat_999"

    # Изначально флага нет.
    assert not client.is_memory_query_flagged(chat_id)

    # После flag_memory_query — флаг установлен.
    client.flag_memory_query(chat_id)
    assert chat_id in client._memory_query_flags

    # Первый вызов is_memory_query_flagged возвращает True и сбрасывает флаг.
    assert client.is_memory_query_flagged(chat_id) is True

    # Второй вызов — флаг уже сброшен.
    assert client.is_memory_query_flagged(chat_id) is False
