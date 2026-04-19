# -*- coding: utf-8 -*-
"""
Тесты для команды !json (форматирование и валидация JSON).

Покрываем:
  - вспомогательная функция _json_extract_text
  - handle_json: format, validate, minify, reply-режим, справка
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _json_extract_text,
    handle_json,
)

# ---------------------------------------------------------------------------
# Вспомогательные fixtures
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    """Создаёт мок бота с _get_command_args."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message(reply_text: str | None = None, reply_caption: str | None = None) -> AsyncMock:
    """Создаёт мок сообщения с опциональным reply."""
    msg = AsyncMock()
    msg.chat = MagicMock()
    msg.chat.id = 42
    if reply_text is not None or reply_caption is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.text = reply_text
        msg.reply_to_message.caption = reply_caption
    else:
        msg.reply_to_message = None
    return msg


# ---------------------------------------------------------------------------
# _json_extract_text
# ---------------------------------------------------------------------------


class TestJsonExtractText:
    """Тесты вспомогательной функции _json_extract_text."""

    def test_returns_args_when_nonempty(self):
        """Если args не пустой — возвращает args."""
        msg = _make_message()
        assert _json_extract_text(msg, '{"a":1}') == '{"a":1}'

    def test_returns_reply_text_when_args_empty(self):
        """Если args пустой и есть reply — возвращает текст reply."""
        msg = _make_message(reply_text='{"key":"value"}')
        assert _json_extract_text(msg, "") == '{"key":"value"}'

    def test_returns_reply_caption_when_text_is_none(self):
        """Если reply.text=None — возвращает reply.caption."""
        msg = _make_message(reply_caption='{"cap":true}')
        msg.reply_to_message.text = None
        assert _json_extract_text(msg, "") == '{"cap":true}'

    def test_returns_none_when_no_args_no_reply(self):
        """Если нет args и нет reply — возвращает None."""
        msg = _make_message()
        assert _json_extract_text(msg, "") is None

    def test_args_has_priority_over_reply(self):
        """args имеет приоритет перед reply."""
        msg = _make_message(reply_text='{"reply":1}')
        assert _json_extract_text(msg, '{"args":2}') == '{"args":2}'


# ---------------------------------------------------------------------------
# handle_json — форматирование (default)
# ---------------------------------------------------------------------------


class TestHandleJsonFormat:
    """Тесты форматирования JSON."""

    @pytest.mark.asyncio
    async def test_format_simple_object(self):
        """!json {"a":1} → pretty-printed JSON в ответе."""
        bot = _make_bot('{"a":1}')
        msg = _make_message()
        await handle_json(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "json" in reply_text.lower() or "```" in reply_text
        # Результат должен содержать отформатированный JSON
        assert '"a"' in reply_text
        assert '"1"' not in reply_text  # значение числовое, не строка
        assert "1" in reply_text

    @pytest.mark.asyncio
    async def test_format_indented_with_2_spaces(self):
        """Форматирование использует 2-пробельные отступы."""
        bot = _make_bot('{"a":1,"b":2}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # json.dumps с indent=2 даёт двойной отступ
        expected = json.dumps({"a": 1, "b": 2}, ensure_ascii=False, indent=2)
        assert expected in reply_text

    @pytest.mark.asyncio
    async def test_format_array(self):
        """!json [1,2,3] → форматированный массив."""
        bot = _make_bot("[1,2,3]")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "1" in reply_text
        assert "2" in reply_text
        assert "3" in reply_text

    @pytest.mark.asyncio
    async def test_format_nested(self):
        """Вложенный JSON форматируется без ошибок."""
        raw = '{"outer":{"inner":[1,2,3]}}'
        bot = _make_bot(raw)
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "outer" in reply_text
        assert "inner" in reply_text

    @pytest.mark.asyncio
    async def test_format_unicode_preserved(self):
        """Unicode (русские символы) сохраняются без экранирования."""
        bot = _make_bot('{"ключ":"значение"}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "ключ" in reply_text
        assert "значение" in reply_text

    @pytest.mark.asyncio
    async def test_format_invalid_json_raises_user_error(self):
        """Невалидный JSON → UserInputError с сообщением об ошибке."""
        bot = _make_bot("{broken json}")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        assert "❌" in exc_info.value.user_message
        assert "невалиден" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_format_error_contains_position(self):
        """Сообщение об ошибке содержит позицию (line/column)."""
        bot = _make_bot('{"a": ,}')
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        err = exc_info.value.user_message
        assert "line" in err or "column" in err or "col" in err.lower()

    @pytest.mark.asyncio
    async def test_format_wrapped_in_code_block(self):
        """Ответ обёрнут в markdown code block."""
        bot = _make_bot('{"x":1}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "```" in reply_text


# ---------------------------------------------------------------------------
# handle_json — reply-режим
# ---------------------------------------------------------------------------


class TestHandleJsonReply:
    """Тесты форматирования JSON из reply-сообщения."""

    @pytest.mark.asyncio
    async def test_format_from_reply_text(self):
        """!json без аргументов в reply → форматирует текст ответа."""
        bot = _make_bot("")
        msg = _make_message(reply_text='{"from":"reply"}')
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "from" in reply_text
        assert "reply" in reply_text

    @pytest.mark.asyncio
    async def test_format_from_reply_caption(self):
        """reply.text=None, reply.caption содержит JSON → форматирует caption."""
        bot = _make_bot("")
        msg = _make_message(reply_caption='{"cap":42}')
        msg.reply_to_message.text = None
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "cap" in reply_text
        assert "42" in reply_text

    @pytest.mark.asyncio
    async def test_reply_with_invalid_json_raises(self):
        """reply содержит невалидный JSON → UserInputError."""
        bot = _make_bot("")
        msg = _make_message(reply_text="это не json!!!")
        with pytest.raises(UserInputError):
            await handle_json(bot, msg)


# ---------------------------------------------------------------------------
# handle_json validate
# ---------------------------------------------------------------------------


class TestHandleJsonValidate:
    """Тесты подкоманды validate."""

    @pytest.mark.asyncio
    async def test_validate_valid_json(self):
        """!json validate <валидный> → ✅ JSON валиден."""
        bot = _make_bot('validate {"ok": true}')
        msg = _make_message()
        await handle_json(bot, msg)
        msg.reply.assert_awaited_once()
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text
        assert "валиден" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_validate_invalid_json(self):
        """!json validate <невалидный> → ❌ с описанием ошибки."""
        bot = _make_bot("validate {bad json}")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "❌" in reply_text
        assert "невалиден" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_validate_error_shows_position(self):
        """Ошибка валидации показывает позицию."""
        bot = _make_bot('validate {"a": }')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "line" in reply_text or "column" in reply_text

    @pytest.mark.asyncio
    async def test_validate_array(self):
        """!json validate [1, 2, 3] → валиден."""
        bot = _make_bot("validate [1, 2, 3]")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text

    @pytest.mark.asyncio
    async def test_validate_empty_object(self):
        """!json validate {} → валиден."""
        bot = _make_bot("validate {}")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text

    @pytest.mark.asyncio
    async def test_validate_missing_comma(self):
        """!json validate {"a":1 "b":2} — пропущена запятая → невалиден."""
        bot = _make_bot('validate {"a":1 "b":2}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "❌" in reply_text

    @pytest.mark.asyncio
    async def test_validate_no_exception_propagation(self):
        """Validate не бросает исключение, только отвечает сообщением."""
        bot = _make_bot("validate BROKEN")
        msg = _make_message()
        # Не должно бросать ничего — просто reply с ошибкой
        await handle_json(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validate_empty_payload_falls_to_reply(self):
        """!json validate без payload — пробует взять из reply."""
        bot = _make_bot("validate")
        msg = _make_message(reply_text='{"valid": true}')
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text


# ---------------------------------------------------------------------------
# handle_json minify
# ---------------------------------------------------------------------------


class TestHandleJsonMinify:
    """Тесты подкоманды minify."""

    @pytest.mark.asyncio
    async def test_minify_removes_whitespace(self):
        """!json minify — убирает пробелы и переносы строк."""
        pretty = '{\n  "a": 1,\n  "b": 2\n}'
        bot = _make_bot(f"minify {pretty}")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        # Минифицированный вид — без лишних пробелов вокруг : и ,
        assert '{"a":1,"b":2}' in reply_text

    @pytest.mark.asyncio
    async def test_minify_preserves_values(self):
        """Минификация сохраняет все значения."""
        bot = _make_bot('minify {"key": "value", "num": 42}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "key" in reply_text
        assert "value" in reply_text
        assert "42" in reply_text

    @pytest.mark.asyncio
    async def test_minify_unicode_preserved(self):
        """Минификация сохраняет Unicode без \\uXXXX экранирования."""
        bot = _make_bot('minify {"русский": "текст"}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "русский" in reply_text
        assert "текст" in reply_text

    @pytest.mark.asyncio
    async def test_minify_invalid_json_raises(self):
        """!json minify <невалидный> → UserInputError."""
        bot = _make_bot("minify {broken}")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        assert "❌" in exc_info.value.user_message
        assert "невалиден" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_minify_array(self):
        """!json minify [1, 2, 3] → [1,2,3]."""
        bot = _make_bot("minify [1, 2, 3]")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "[1,2,3]" in reply_text

    @pytest.mark.asyncio
    async def test_minify_wrapped_in_code_block(self):
        """Результат минификации обёрнут в code block."""
        bot = _make_bot('minify {"a":1}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "```" in reply_text

    @pytest.mark.asyncio
    async def test_minify_empty_payload_uses_reply(self):
        """!json minify без payload берёт из reply."""
        bot = _make_bot("minify")
        msg = _make_message(reply_text='{"a": 1}')
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert '{"a":1}' in reply_text


# ---------------------------------------------------------------------------
# handle_json — справка (нет аргументов, нет reply)
# ---------------------------------------------------------------------------


class TestHandleJsonHelp:
    """Тесты вывода справки."""

    @pytest.mark.asyncio
    async def test_no_args_no_reply_raises_user_input_error(self):
        """!json без аргументов и без reply → UserInputError со справкой."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        err = exc_info.value.user_message
        assert "validate" in err
        assert "minify" in err

    @pytest.mark.asyncio
    async def test_help_mentions_reply_mode(self):
        """Справка упоминает reply-режим."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        assert "reply" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_help_mentions_format_command(self):
        """Справка упоминает базовую команду формата."""
        bot = _make_bot("")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        assert "!json" in exc_info.value.user_message


# ---------------------------------------------------------------------------
# Граничные случаи
# ---------------------------------------------------------------------------


class TestHandleJsonEdgeCases:
    """Граничные случаи для handle_json."""

    @pytest.mark.asyncio
    async def test_format_boolean_true(self):
        """JSON с булевым значением true форматируется."""
        bot = _make_bot('{"flag": true}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "flag" in reply_text
        assert "true" in reply_text

    @pytest.mark.asyncio
    async def test_format_null_value(self):
        """JSON с null значением форматируется."""
        bot = _make_bot('{"val": null}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "null" in reply_text

    @pytest.mark.asyncio
    async def test_format_already_pretty(self):
        """Уже форматированный JSON проходит без ошибок."""
        pretty = '{\n  "key": "val"\n}'
        bot = _make_bot(pretty)
        msg = _make_message()
        await handle_json(bot, msg)
        msg.reply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_validate_number_as_root(self):
        """JSON с числом в корне валиден (по RFC 7159)."""
        bot = _make_bot("validate 42")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text

    @pytest.mark.asyncio
    async def test_minify_deeply_nested(self):
        """Глубоко вложенный JSON минифицируется без ошибок."""
        nested = '{"a": {"b": {"c": {"d": 1}}}}'
        bot = _make_bot(f"minify {nested}")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert '{"a":{"b":{"c":{"d":1}}}}' in reply_text

    @pytest.mark.asyncio
    async def test_format_string_as_root(self):
        """JSON со строкой в корне форматируется."""
        bot = _make_bot('"hello world"')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "hello world" in reply_text

    @pytest.mark.asyncio
    async def test_error_message_format(self):
        """Формат ошибки: ❌ JSON невалиден: <msg>: line X column Y."""
        bot = _make_bot("{bad}")
        msg = _make_message()
        with pytest.raises(UserInputError) as exc_info:
            await handle_json(bot, msg)
        err = exc_info.value.user_message
        # Проверяем точный формат ошибки
        assert err.startswith("❌ JSON невалиден:")
        assert "line" in err
        assert "column" in err

    @pytest.mark.asyncio
    async def test_validate_error_message_format(self):
        """validate: формат ошибки содержит line/column."""
        bot = _make_bot("validate {bad}")
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert reply_text.startswith("❌ JSON невалиден:")
        assert "line" in reply_text
        assert "column" in reply_text

    @pytest.mark.asyncio
    async def test_subcommand_case_insensitive(self):
        """Подкоманды нечувствительны к регистру (VALIDATE, Minify)."""
        # VALIDATE
        bot = _make_bot('VALIDATE {"ok": 1}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert "✅" in reply_text

    @pytest.mark.asyncio
    async def test_minify_subcommand_case_insensitive(self):
        """Подкоманда MINIFY работает корректно."""
        bot = _make_bot('MINIFY {"a": 1}')
        msg = _make_message()
        await handle_json(bot, msg)
        reply_text = msg.reply.call_args[0][0]
        assert '{"a":1}' in reply_text
