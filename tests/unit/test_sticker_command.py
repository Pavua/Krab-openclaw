# -*- coding: utf-8 -*-
"""
Тесты команды !sticker (handle_sticker) из src/handlers/command_handlers.py.

Покрытие:
1. !sticker save <name> — сохраняет file_id стикера из reply
2. !sticker save без reply → UserInputError
3. !sticker save без имени → UserInputError
4. !sticker <name> — отправляет стикер по имени
5. !sticker <name> несуществующий → UserInputError
6. !sticker list — выводит список
7. !sticker list пустой — сообщение об отсутствии
8. !sticker del <name> — удаляет
9. !sticker del несуществующее имя → UserInputError
10. !sticker del без имени → UserInputError
11. !sticker (без аргументов) — эквивалент list
12. Сохранение файла идёт в корректный путь
13. !sticker send — удаляет исходное сообщение после отправки
14. !sticker save перезаписывает уже сохранённый стикер
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_sticker

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot.client.send_sticker = AsyncMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_sticker_message(file_id: str = "STICKER_FILE_ID_AAA") -> MagicMock:
    """Создаёт сообщение-стикер для reply."""
    msg = MagicMock()
    msg.sticker = SimpleNamespace(file_id=file_id)
    return msg


def _make_message(
    command_args: str = "",
    reply_sticker_file_id: str | None = None,
    chat_id: int = 999,
) -> tuple[MagicMock, MagicMock]:
    """Возвращает (bot, message)."""
    bot = _make_bot(command_args)
    msg = MagicMock()
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    if reply_sticker_file_id is not None:
        msg.reply_to_message = _make_sticker_message(reply_sticker_file_id)
    else:
        msg.reply_to_message = None
    return bot, msg


# ---------------------------------------------------------------------------
# Тесты !sticker save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_save_stores_file_id(tmp_path) -> None:
    """`!sticker save foo` в reply на стикер → file_id записывается в JSON."""
    stickers_path = tmp_path / "saved_stickers.json"

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save foo", reply_sticker_file_id="FILE_AAA")
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    assert "foo" in msg.reply.await_args.args[0]
    data = json.loads(stickers_path.read_text())
    assert data["foo"] == "FILE_AAA"


@pytest.mark.asyncio
async def test_sticker_save_without_reply_raises(tmp_path) -> None:
    """`!sticker save foo` без reply → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save foo", reply_sticker_file_id=None)
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


@pytest.mark.asyncio
async def test_sticker_save_without_name_raises(tmp_path) -> None:
    """`!sticker save` без имени → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save", reply_sticker_file_id="FILE_BBB")
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


@pytest.mark.asyncio
async def test_sticker_save_overwrites_existing(tmp_path) -> None:
    """`!sticker save foo` второй раз перезаписывает file_id."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "OLD_FILE"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save foo", reply_sticker_file_id="NEW_FILE")
        await handle_sticker(bot, msg)

    data = json.loads(stickers_path.read_text())
    assert data["foo"] == "NEW_FILE"


@pytest.mark.asyncio
async def test_sticker_save_reply_without_sticker_raises(tmp_path) -> None:
    """`!sticker save foo` — reply есть, но это не стикер → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save foo", reply_sticker_file_id=None)
        # Создаём reply без стикера
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.sticker = None
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


# ---------------------------------------------------------------------------
# Тесты !sticker <name> (отправка)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_send_existing(tmp_path) -> None:
    """`!sticker foo` → send_sticker вызывается с нужным file_id."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("foo")
        await handle_sticker(bot, msg)

    bot.client.send_sticker.assert_awaited_once_with(999, "FILE_FOO")


@pytest.mark.asyncio
async def test_sticker_send_unknown_raises(tmp_path) -> None:
    """`!sticker notexist` → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("notexist")
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


@pytest.mark.asyncio
async def test_sticker_send_deletes_command(tmp_path) -> None:
    """`!sticker foo` → после отправки стикера исходная команда удаляется."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("foo")
        await handle_sticker(bot, msg)

    msg.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_sticker_send_delete_failure_is_silent(tmp_path) -> None:
    """`!sticker foo` — ошибка delete не ломает команду."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("foo")
        msg.delete = AsyncMock(side_effect=Exception("no rights"))
        await handle_sticker(bot, msg)  # не должно упасть

    bot.client.send_sticker.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты !sticker list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_list_shows_names(tmp_path) -> None:
    """`!sticker list` → перечисляет имена."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"alpha": "F1", "beta": "F2"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("list")
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "alpha" in text
    assert "beta" in text


@pytest.mark.asyncio
async def test_sticker_list_empty(tmp_path) -> None:
    """`!sticker list` при пустом файле → сообщение об отсутствии."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("list")
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "Нет" in text or "нет" in text or "save" in text.lower()


@pytest.mark.asyncio
async def test_sticker_no_args_is_list(tmp_path) -> None:
    """`!sticker` без аргументов — эквивалент list."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"meme": "FILE_MEME"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("")  # нет аргументов
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    assert "meme" in msg.reply.await_args.args[0]


# ---------------------------------------------------------------------------
# Тесты !sticker del
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_del_removes_entry(tmp_path) -> None:
    """`!sticker del foo` → запись удаляется из файла."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO", "bar": "FILE_BAR"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("del foo")
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    data = json.loads(stickers_path.read_text())
    assert "foo" not in data
    assert "bar" in data


@pytest.mark.asyncio
async def test_sticker_del_unknown_raises(tmp_path) -> None:
    """`!sticker del missing` → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("del missing")
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


@pytest.mark.asyncio
async def test_sticker_del_without_name_raises(tmp_path) -> None:
    """`!sticker del` без имени → UserInputError."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("del")
        with pytest.raises(UserInputError):
            await handle_sticker(bot, msg)


# ---------------------------------------------------------------------------
# Тесты устойчивости (файл отсутствует, повреждён)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sticker_list_no_file(tmp_path) -> None:
    """`!sticker list` без файла → сообщение об отсутствии."""
    stickers_path = tmp_path / "saved_stickers.json"
    # файл не создаём — он не существует

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("list")
        await handle_sticker(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "Нет" in text or "save" in text.lower()


@pytest.mark.asyncio
async def test_sticker_save_creates_dir(tmp_path) -> None:
    """`!sticker save` создаёт родительский каталог если его нет."""
    stickers_path = tmp_path / "nested" / "dir" / "saved_stickers.json"

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("save foo", reply_sticker_file_id="FILE_X")
        await handle_sticker(bot, msg)

    assert stickers_path.exists()
    data = json.loads(stickers_path.read_text())
    assert data["foo"] == "FILE_X"


@pytest.mark.asyncio
async def test_sticker_send_case_insensitive_name(tmp_path) -> None:
    """`!sticker FOO` работает как `!sticker foo` (нижний регистр)."""
    stickers_path = tmp_path / "saved_stickers.json"
    stickers_path.write_text(json.dumps({"foo": "FILE_FOO"}))

    with patch.object(cmd_module, "_STICKERS_FILE", stickers_path):
        bot, msg = _make_message("FOO")
        await handle_sticker(bot, msg)

    bot.client.send_sticker.assert_awaited_once_with(999, "FILE_FOO")
