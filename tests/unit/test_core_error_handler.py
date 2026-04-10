# -*- coding: utf-8 -*-
"""Тесты для src/core/error_handler.py — safe_handler, get_error_stats, reset_error_stats."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrogram.errors import (
    ChatWriteForbidden,
    FloodWait,
    MessageNotModified,
    UserNotParticipant,
)

from src.core.error_handler import get_error_stats, reset_error_stats, safe_handler

# ---------------------------------------------------------------------------
# Вспомогательные фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_error_counts():
    """Сбрасываем счётчики до и после каждого теста."""
    reset_error_stats()
    yield
    reset_error_stats()


def _make_update(can_reply: bool = False) -> MagicMock:
    """Создаёт фейковый Pyrogram update-объект."""
    update = MagicMock()
    if can_reply:
        update.reply_text = AsyncMock()
    else:
        del update.reply_text  # убираем атрибут чтобы hasattr вернул False
    return update


# ---------------------------------------------------------------------------
# safe_handler — нормальный путь
# ---------------------------------------------------------------------------


class TestSafeHandlerSuccess:
    """Декоратор safe_handler на happy-path."""

    @pytest.mark.asyncio
    async def test_passes_return_value_through(self):
        """Результат оборачиваемой функции возвращается без изменений."""

        @safe_handler
        async def handler(client, update):
            return 42

        result = await handler(MagicMock(), MagicMock())
        assert result == 42

    @pytest.mark.asyncio
    async def test_preserves_function_name(self):
        """functools.wraps сохраняет имя функции."""

        @safe_handler
        async def my_special_handler(client, update):
            pass

        assert my_special_handler.__name__ == "my_special_handler"

    @pytest.mark.asyncio
    async def test_passes_extra_args_and_kwargs(self):
        """Дополнительные *args и **kwargs передаются в обёрнутую функцию."""
        received = {}

        @safe_handler
        async def handler(client, update, *args, **kwargs):
            received["args"] = args
            received["kwargs"] = kwargs

        await handler(MagicMock(), MagicMock(), "a", "b", key="val")
        assert received["args"] == ("a", "b")
        assert received["kwargs"] == {"key": "val"}

    @pytest.mark.asyncio
    async def test_no_error_stats_on_success(self):
        """При успешном выполнении счётчики ошибок не растут."""

        @safe_handler
        async def handler(client, update):
            return "ok"

        await handler(MagicMock(), MagicMock())
        assert get_error_stats() == {}


# ---------------------------------------------------------------------------
# safe_handler — FloodWait
# ---------------------------------------------------------------------------


class TestSafeHandlerFloodWait:
    """Обработка FloodWait: ждём, счётчик растёт, хэндлер не повторяется."""

    @pytest.mark.asyncio
    async def test_flood_wait_sleeps_and_returns_none(self):
        """FloodWait → sleep(value+1), возврат None (не повторяем вызов)."""
        call_count = 0

        @safe_handler
        async def handler(client, update):
            nonlocal call_count
            call_count += 1
            raise FloodWait(5)

        with patch("src.core.error_handler.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await handler(MagicMock(), MagicMock())

        assert result is None
        mock_sleep.assert_awaited_once_with(6)  # value=5 + 1 буфер
        assert call_count == 1  # вызвали ровно один раз, НЕ повторяли

    @pytest.mark.asyncio
    async def test_flood_wait_increments_counter(self):
        """FloodWait увеличивает счётчик _error_counts['FloodWait']."""

        @safe_handler
        async def handler(client, update):
            raise FloodWait(1)

        with patch("src.core.error_handler.asyncio.sleep", new_callable=AsyncMock):
            await handler(MagicMock(), MagicMock())
            await handler(MagicMock(), MagicMock())

        assert get_error_stats()["FloodWait"] == 2

    @pytest.mark.asyncio
    async def test_flood_wait_does_not_call_reply(self):
        """FloodWait не пытается ответить пользователю."""
        update = _make_update(can_reply=True)

        @safe_handler
        async def handler(client, update_):
            raise FloodWait(1)

        with patch("src.core.error_handler.asyncio.sleep", new_callable=AsyncMock):
            await handler(MagicMock(), update)

        update.reply_text.assert_not_called()


# ---------------------------------------------------------------------------
# safe_handler — тихие исключения (без счётчика / без ответа)
# ---------------------------------------------------------------------------


class TestSafeHandlerSilentExceptions:
    """MessageNotModified, ChatWriteForbidden, UserNotParticipant — тихие."""

    @pytest.mark.asyncio
    async def test_message_not_modified_silent(self):
        """MessageNotModified игнорируется без записи в счётчик."""

        @safe_handler
        async def handler(client, update):
            raise MessageNotModified()

        await handler(MagicMock(), MagicMock())
        assert get_error_stats() == {}

    @pytest.mark.asyncio
    async def test_chat_write_forbidden_silent(self):
        """ChatWriteForbidden логируется warning, счётчик не меняется."""

        @safe_handler
        async def handler(client, update):
            raise ChatWriteForbidden()

        await handler(MagicMock(), MagicMock())
        assert get_error_stats() == {}

    @pytest.mark.asyncio
    async def test_user_not_participant_silent(self):
        """UserNotParticipant — только warning в лог, статистика чистая."""

        @safe_handler
        async def handler(client, update):
            raise UserNotParticipant()

        await handler(MagicMock(), MagicMock())
        assert get_error_stats() == {}


# ---------------------------------------------------------------------------
# safe_handler — RecursionError
# ---------------------------------------------------------------------------


class TestSafeHandlerRecursionError:
    """RecursionError ловится явно, чтобы бот продолжал работу."""

    @pytest.mark.asyncio
    async def test_recursion_error_increments_counter(self):
        """RecursionError → счётчик 'RecursionError' +1."""

        @safe_handler
        async def handler(client, update):
            raise RecursionError

        await handler(MagicMock(), MagicMock())
        assert get_error_stats()["RecursionError"] == 1

    @pytest.mark.asyncio
    async def test_recursion_error_returns_none(self):
        """RecursionError → возврат None, бот не падает."""

        @safe_handler
        async def handler(client, update):
            raise RecursionError

        result = await handler(MagicMock(), MagicMock())
        assert result is None


# ---------------------------------------------------------------------------
# safe_handler — общие исключения
# ---------------------------------------------------------------------------


class TestSafeHandlerGenericException:
    """Произвольные Exception — логируем, уведомляем, считаем."""

    @pytest.mark.asyncio
    async def test_generic_exception_increments_by_type(self):
        """Exception класс записывается в счётчик по имени типа."""

        @safe_handler
        async def handler(client, update):
            raise ValueError("bad value")

        await handler(MagicMock(), _make_update())
        assert get_error_stats()["ValueError"] == 1

    @pytest.mark.asyncio
    async def test_multiple_different_exceptions_counted_separately(self):
        """Разные типы исключений имеют отдельные счётчики."""

        @safe_handler
        async def handler(client, update, exc_cls):
            raise exc_cls("err")

        fake = _make_update()
        await handler(MagicMock(), fake, ValueError)
        await handler(MagicMock(), fake, RuntimeError)
        await handler(MagicMock(), fake, ValueError)

        stats = get_error_stats()
        assert stats["ValueError"] == 2
        assert stats["RuntimeError"] == 1

    @pytest.mark.asyncio
    async def test_generic_exception_replies_to_message(self):
        """При наличии reply_text вызывается уведомление пользователя."""
        update = _make_update(can_reply=True)

        @safe_handler
        async def handler(client, update_):
            raise KeyError("missing")

        await handler(MagicMock(), update)
        update.reply_text.assert_awaited_once()
        call_text = update.reply_text.call_args[0][0]
        assert "KeyError" in call_text

    @pytest.mark.asyncio
    async def test_generic_exception_no_reply_if_no_attribute(self):
        """Если у update нет reply_text — нет попытки ответить."""
        update = _make_update(can_reply=False)

        @safe_handler
        async def handler(client, update_):
            raise TypeError("oops")

        result = await handler(MagicMock(), update)
        assert result is None  # не упало

    @pytest.mark.asyncio
    async def test_reply_failure_does_not_propagate(self):
        """Если reply_text бросает — исключение глотается, бот не падает."""
        update = _make_update(can_reply=True)
        update.reply_text.side_effect = RuntimeError("network dead")

        @safe_handler
        async def handler(client, update_):
            raise ValueError("original")

        result = await handler(MagicMock(), update)
        assert result is None


# ---------------------------------------------------------------------------
# get_error_stats / reset_error_stats
# ---------------------------------------------------------------------------


class TestErrorStats:
    """Публичный API статистики ошибок."""

    def test_returns_copy_not_original(self):
        """get_error_stats возвращает копию — мутация не влияет на внутренний dict."""
        stats = get_error_stats()
        stats["injected"] = 999
        assert "injected" not in get_error_stats()

    def test_reset_clears_all_counts(self):
        """reset_error_stats обнуляет все счётчики."""
        from src.core.error_handler import _error_counts

        _error_counts["FakeError"] = 5
        reset_error_stats()
        assert get_error_stats() == {}

    def test_initial_state_empty(self):
        """После reset счётчики пусты."""
        assert get_error_stats() == {}
