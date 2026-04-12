# -*- coding: utf-8 -*-
"""
Тесты для !run — выполнение Python-кода в subprocess (owner-only).
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_run


# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_access_profile(level: AccessLevel = AccessLevel.OWNER) -> SimpleNamespace:
    return SimpleNamespace(level=level)


def _make_bot(
    cmd_args: str = "",
    access_level: AccessLevel = AccessLevel.OWNER,
    reply_to_text: str | None = None,
) -> SimpleNamespace:
    """Создаёт минимальный mock бота."""
    profile = _make_access_profile(access_level)
    bot = SimpleNamespace(
        _get_access_profile=lambda user: profile,
        _get_command_args=lambda msg: cmd_args,
    )
    return bot


def _make_message(
    text: str = "!run",
    reply_text: str | None = None,
) -> SimpleNamespace:
    """Создаёт минимальный mock Message."""
    if reply_text is not None:
        reply_to_message = SimpleNamespace(text=reply_text, caption=None)
    else:
        reply_to_message = None

    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=42),
        reply_to_message=reply_to_message,
        reply=AsyncMock(),
        chat=SimpleNamespace(id=123),
    )


def _fake_subprocess(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0):
    """Возвращает mock для asyncio.create_subprocess_exec."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.kill = MagicMock()
    # communicate возвращает (stdout, stderr)
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    return proc


# ---------------------------------------------------------------------------
# Проверка доступа: только owner
# ---------------------------------------------------------------------------


class TestRunAccessControl:
    """handle_run отклоняет не-owner пользователей."""

    @pytest.mark.asyncio
    async def test_non_owner_raises(self) -> None:
        bot = _make_bot(cmd_args="print('hi')", access_level=AccessLevel.GUEST)
        msg = _make_message(text="!run print('hi')")
        with pytest.raises(UserInputError) as exc_info:
            await handle_run(bot, msg)
        assert "владельцу" in exc_info.value.user_message.lower() or "owner" in exc_info.value.user_message.lower()

    @pytest.mark.asyncio
    async def test_full_user_raises(self) -> None:
        bot = _make_bot(cmd_args="1+1", access_level=AccessLevel.FULL)
        msg = _make_message()
        with pytest.raises(UserInputError):
            await handle_run(bot, msg)

    @pytest.mark.asyncio
    async def test_owner_allowed(self) -> None:
        """Owner может вызвать команду (не UserInputError на проверке доступа)."""
        bot = _make_bot(cmd_args="", access_level=AccessLevel.OWNER)
        msg = _make_message()
        # Без кода должен бросить UserInputError о пустом коде, но не о доступе
        with pytest.raises(UserInputError) as exc_info:
            await handle_run(bot, msg)
        # Ошибка должна быть о пустом коде, а не о доступе
        assert "владельцу" not in exc_info.value.user_message.lower() or "run" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# Пустой код
# ---------------------------------------------------------------------------


class TestRunEmptyCode:
    """Если код не передан — UserInputError с подсказкой."""

    @pytest.mark.asyncio
    async def test_no_args_no_reply(self) -> None:
        bot = _make_bot(cmd_args="")
        msg = _make_message(reply_text=None)
        with pytest.raises(UserInputError) as exc_info:
            await handle_run(bot, msg)
        assert "!run" in exc_info.value.user_message

    @pytest.mark.asyncio
    async def test_whitespace_args_no_reply(self) -> None:
        bot = _make_bot(cmd_args="   ")
        msg = _make_message(reply_text=None)
        with pytest.raises(UserInputError):
            await handle_run(bot, msg)

    @pytest.mark.asyncio
    async def test_no_args_empty_reply_text(self) -> None:
        bot = _make_bot(cmd_args="")
        msg = _make_message(reply_text="")
        with pytest.raises(UserInputError):
            await handle_run(bot, msg)


# ---------------------------------------------------------------------------
# Получение кода из reply
# ---------------------------------------------------------------------------


class TestRunCodeFromReply:
    """Код берётся из reply-сообщения если аргумент пуст."""

    @pytest.mark.asyncio
    async def test_code_from_reply_text(self) -> None:
        bot = _make_bot(cmd_args="")
        msg = _make_message(reply_text="print('from reply')")

        proc = _fake_subprocess(stdout=b"from reply\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await handle_run(bot, msg)

        mock_exec.assert_called_once()
        msg.reply.assert_awaited_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "from reply" in reply_text

    @pytest.mark.asyncio
    async def test_direct_args_preferred_over_reply(self) -> None:
        """Если аргументы есть — reply игнорируется."""
        bot = _make_bot(cmd_args="print('direct')")
        msg = _make_message(reply_text="print('from reply')")

        proc = _fake_subprocess(stdout=b"direct\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "direct" in reply_text
        assert "from reply" not in reply_text


# ---------------------------------------------------------------------------
# Вывод stdout
# ---------------------------------------------------------------------------


class TestRunStdout:
    """Команда возвращает stdout в форматированном блоке кода."""

    @pytest.mark.asyncio
    async def test_stdout_wrapped_in_code_block(self) -> None:
        bot = _make_bot(cmd_args="print('hello')")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"hello\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "hello" in reply_text
        assert "```" in reply_text

    @pytest.mark.asyncio
    async def test_multiline_stdout(self) -> None:
        bot = _make_bot(cmd_args="print('line1\\nline2')")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"line1\nline2\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "line1" in reply_text
        assert "line2" in reply_text

    @pytest.mark.asyncio
    async def test_expression_result_shown(self) -> None:
        """Выражение 2**10 должно вернуть 1024."""
        bot = _make_bot(cmd_args="2**10")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"1024\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "1024" in reply_text


# ---------------------------------------------------------------------------
# Вывод stderr
# ---------------------------------------------------------------------------


class TestRunStderr:
    """Stderr отображается с предупреждением."""

    @pytest.mark.asyncio
    async def test_stderr_shown_with_warning(self) -> None:
        bot = _make_bot(cmd_args="import sys; sys.stderr.write('err')")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"", stderr=b"err")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "err" in reply_text
        assert "stderr" in reply_text.lower() or "⚠️" in reply_text

    @pytest.mark.asyncio
    async def test_both_stdout_and_stderr(self) -> None:
        bot = _make_bot(cmd_args="some code")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"out", stderr=b"errout")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "out" in reply_text
        assert "errout" in reply_text


# ---------------------------------------------------------------------------
# Пустой вывод
# ---------------------------------------------------------------------------


class TestRunEmptyOutput:
    """Если нет ни stdout, ни stderr — сообщение об успехе."""

    @pytest.mark.asyncio
    async def test_no_output_success_message(self) -> None:
        bot = _make_bot(cmd_args="x = 1")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"", stderr=b"", returncode=0)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        # Должно содержать сообщение об успешном выполнении
        assert "exit" in reply_text.lower() or "выполнен" in reply_text.lower() or "✅" in reply_text


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestRunTimeout:
    """Timeout завершает процесс и возвращает предупреждение."""

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self) -> None:
        bot = _make_bot(cmd_args="import time; time.sleep(100)")
        msg = _make_message()

        proc = MagicMock()
        proc.kill = MagicMock()
        # communicate зависает, поэтому TimeoutError
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        proc.kill.assert_called_once()
        reply_text: str = msg.reply.call_args[0][0]
        assert "timeout" in reply_text.lower() or "5" in reply_text or "прерван" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_timeout_replies_to_message(self) -> None:
        """После timeout бот отвечает на сообщение."""
        bot = _make_bot(cmd_args="while True: pass")
        msg = _make_message()

        proc = MagicMock()
        proc.kill = MagicMock()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        msg.reply.assert_awaited_once()


# ---------------------------------------------------------------------------
# Subprocess: изоляция
# ---------------------------------------------------------------------------


class TestRunSubprocessIsolation:
    """Код запускается через subprocess, не в основном процессе."""

    @pytest.mark.asyncio
    async def test_subprocess_exec_called(self) -> None:
        bot = _make_bot(cmd_args="print(42)")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"42\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await handle_run(bot, msg)

        mock_exec.assert_called_once()
        # Первый аргумент — sys.executable
        call_args = mock_exec.call_args[0]
        assert call_args[0] == sys.executable
        # Второй — "-c"
        assert call_args[1] == "-c"

    @pytest.mark.asyncio
    async def test_subprocess_env_kwarg_passed(self) -> None:
        """Subprocess вызывается с keyword-аргументом env."""
        bot = _make_bot(cmd_args="print(1)")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"1\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await handle_run(bot, msg)

        # Проверяем что env передан как kwarg
        call_kwargs = mock_exec.call_args[1]
        assert "env" in call_kwargs

    @pytest.mark.asyncio
    async def test_wait_for_timeout_5_seconds(self) -> None:
        """asyncio.wait_for вызывается с timeout=5."""
        bot = _make_bot(cmd_args="pass")
        msg = _make_message()

        proc = _fake_subprocess()

        captured_timeout = {}

        original_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout):
            captured_timeout["value"] = timeout
            return await original_wait_for(coro, timeout)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with patch("asyncio.wait_for", side_effect=spy_wait_for):
                await handle_run(bot, msg)

        assert captured_timeout.get("value") == 5.0


# ---------------------------------------------------------------------------
# Expression vs Statement
# ---------------------------------------------------------------------------


class TestRunExpressionWrapping:
    """Выражение (expression) оборачивается в print автоматически."""

    @pytest.mark.asyncio
    async def test_expression_passed_as_code_to_subprocess(self) -> None:
        """2**100 должен передаться subprocess в виде, который выведет результат."""
        bot = _make_bot(cmd_args="2**100")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"1267650600228229401496703205376\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await handle_run(bot, msg)

        # Переданный код должен содержать 2**100
        call_code: str = mock_exec.call_args[0][2]
        assert "2**100" in call_code

    @pytest.mark.asyncio
    async def test_statement_passed_as_is(self) -> None:
        """print('hi') — statement, передаётся как есть."""
        bot = _make_bot(cmd_args="print('hi')")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"hi\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await handle_run(bot, msg)

        call_code: str = mock_exec.call_args[0][2]
        assert "print" in call_code


# ---------------------------------------------------------------------------
# Unicode в выводе
# ---------------------------------------------------------------------------


class TestRunUnicode:
    """Вывод с unicode обрабатывается корректно."""

    @pytest.mark.asyncio
    async def test_unicode_stdout(self) -> None:
        bot = _make_bot(cmd_args="print('Привет, мир!')")
        msg = _make_message()

        proc = _fake_subprocess(stdout="Привет, мир!\n".encode("utf-8"))
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await handle_run(bot, msg)

        reply_text: str = msg.reply.call_args[0][0]
        assert "Привет" in reply_text

    @pytest.mark.asyncio
    async def test_invalid_utf8_handled(self) -> None:
        """Некорректный UTF-8 декодируется с replace."""
        bot = _make_bot(cmd_args="pass")
        msg = _make_message()

        proc = _fake_subprocess(stdout=b"\xff\xfe invalid")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            # Не должен бросать исключение
            await handle_run(bot, msg)

        msg.reply.assert_awaited_once()
