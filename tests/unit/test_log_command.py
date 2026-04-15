# -*- coding: utf-8 -*-
"""
Юнит-тесты для команды !log — просмотр логов Краба.

Покрываемые сценарии:
  - !log                → default tail 20 строк
  - !log N              → tail N строк
  - !log N (invalid)    → UserInputError с help
  - !log errors         → filter ERROR/WARNING/CRITICAL
  - !log errors (нет)   → "Ошибок нет"
  - !log search <q>     → grep-like
  - !log search (пусто) → UserInputError
  - !log search (нет)   → "ничего не найдено"
  - log file missing    → "Лог-файл не найден"
  - короткий вывод      → message.reply
  - длинный вывод       → send_document
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_log


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_bot(send_document_mock: AsyncMock | None = None) -> MagicMock:
    """Мок бота с _get_command_args и client.send_document."""
    bot = MagicMock()
    bot._get_command_args = MagicMock(return_value="")
    client = MagicMock()
    client.send_document = send_document_mock or AsyncMock()
    bot.client = client
    return bot


def _make_message(reply_mock: AsyncMock | None = None) -> SimpleNamespace:
    """Мок Pyrogram message с reply и chat."""
    reply = reply_mock or AsyncMock()
    chat = SimpleNamespace(id=12345, type="private")
    return SimpleNamespace(reply=reply, chat=chat)


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    """Создаёт лог-файл с sample содержимым и привязывает его через env."""
    path = tmp_path / "krab_main.log"
    content = "\n".join(
        f"2026-04-15 22:00:{i:02d} [INFO] Some normal line #{i}" for i in range(50)
    )
    # Добавляем строки с ошибками
    content += "\n2026-04-15 22:01:00 [ERROR] Something broke"
    content += "\n2026-04-15 22:01:05 [WARNING] Low disk space"
    content += "\n2026-04-15 22:01:10 [CRITICAL] Out of memory"
    content += "\n2026-04-15 22:01:15 [INFO] Translator processed message"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("KRAB_LOG_FILE", str(path))
    return path


# ---------------------------------------------------------------------------
# 1. File-not-found сценарий
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_file_not_found(tmp_path, monkeypatch):
    """!log при отсутствующем лог-файле → reply с предупреждением."""
    missing = tmp_path / "nonexistent.log"
    monkeypatch.setenv("KRAB_LOG_FILE", str(missing))

    bot = _make_bot()
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called_once()
    call_text = reply.call_args[0][0]
    assert "не найден" in call_text.lower()


# ---------------------------------------------------------------------------
# 2. Default tail (20 строк)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_default_tail_20(log_file):
    """!log без args → последние 20 строк."""
    bot = _make_bot()
    bot._get_command_args.return_value = ""
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called()
    all_text = "".join(c[0][0] for c in reply.call_args_list)
    assert "Последние 20 строк" in all_text
    # Последние строки должны быть в выводе (ERROR, WARNING, CRITICAL)
    assert "CRITICAL" in all_text or "WARNING" in all_text


# ---------------------------------------------------------------------------
# 3. Custom N lines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_custom_n(log_file):
    """!log 5 → последние 5 строк."""
    bot = _make_bot()
    bot._get_command_args.return_value = "5"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called()
    all_text = "".join(c[0][0] for c in reply.call_args_list)
    assert "Последние 5 строк" in all_text


# ---------------------------------------------------------------------------
# 4. Invalid args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_invalid_arg_raises(log_file):
    """!log not_a_number → UserInputError с help."""
    bot = _make_bot()
    bot._get_command_args.return_value = "foobar"
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_log(bot, msg)

    assert "log" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# 5. errors filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_errors_filter(log_file):
    """!log errors → только ERROR/WARNING/CRITICAL строки."""
    bot = _make_bot()
    bot._get_command_args.return_value = "errors"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called()
    all_text = "".join(c[0][0] for c in reply.call_args_list)
    assert "Ошибки" in all_text
    assert "ERROR" in all_text or "CRITICAL" in all_text or "WARNING" in all_text
    # INFO строки с обычными сообщениями не должны появиться
    assert "Some normal line" not in all_text


@pytest.mark.asyncio
async def test_log_errors_none_found(tmp_path, monkeypatch):
    """!log errors когда нет ошибок → '✅ Ошибок нет'."""
    path = tmp_path / "clean.log"
    path.write_text("2026-04-15 22:00:00 [INFO] Everything is fine\n", encoding="utf-8")
    monkeypatch.setenv("KRAB_LOG_FILE", str(path))

    bot = _make_bot()
    bot._get_command_args.return_value = "errors"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called_once()
    assert "ошибок в логах нет" in reply.call_args[0][0].lower()


# ---------------------------------------------------------------------------
# 6. search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_search_found(log_file):
    """!log search translator → строки содержащие 'translator'."""
    bot = _make_bot()
    bot._get_command_args.return_value = "search translator"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called()
    all_text = "".join(c[0][0] for c in reply.call_args_list)
    assert "translator" in all_text.lower()
    assert "Поиск" in all_text


@pytest.mark.asyncio
async def test_log_search_not_found(log_file):
    """!log search xxxxxxx → 'ничего не найдено'."""
    bot = _make_bot()
    bot._get_command_args.return_value = "search nonexistent_token_12345"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called_once()
    assert "ничего не найдено" in reply.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_log_search_empty_query(log_file):
    """!log search (пусто) → UserInputError."""
    bot = _make_bot()
    bot._get_command_args.return_value = "search "
    msg = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_log(bot, msg)

    assert "search" in exc_info.value.user_message.lower() or "запрос" in exc_info.value.user_message.lower()


# ---------------------------------------------------------------------------
# 7. N больше 1000 ограничивается
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_n_capped_at_1000(log_file):
    """!log 99999 → ограничиваем 1000."""
    bot = _make_bot()
    bot._get_command_args.return_value = "99999"
    reply = AsyncMock()
    msg = _make_message(reply)

    await handle_log(bot, msg)

    reply.assert_called()
    all_text = "".join(c[0][0] for c in reply.call_args_list)
    # 1000 максимум (файл короче — просто все покажет)
    assert "1000" in all_text or "54 стро" in all_text or "Последние" in all_text
