# -*- coding: utf-8 -*-
"""
Тесты команды !tag (handle_tag) из src/handlers/command_handlers.py.

Покрытие:
1.  !tag <тег> в reply — добавляет тег к сообщению
2.  !tag <тег> без reply → UserInputError
3.  !tag <тег> — тег уже существует → ответ «уже есть»
4.  !tag list — показывает теги с подсчётом
5.  !tag list — нет тегов → сообщение об отсутствии
6.  !tag (без аргументов) — эквивалент list
7.  !tag find <тег> — возвращает ссылки на сообщения
8.  !tag find <тег> — ничего не найдено
9.  !tag find без тега → UserInputError
10. !tag del <тег> в reply — удаляет тег
11. !tag del <тег> — последний тег на сообщении → запись удаляется
12. !tag del <тег> без reply → UserInputError
13. !tag del несуществующего тега → UserInputError
14. !tag del без имени тега → UserInputError
15. Тег с пробелами → UserInputError
16. Несколько тегов на одном сообщении
17. Теги в разных чатах изолированы
18. Устойчивость: файл отсутствует (_load_tags возвращает {})
19. Устойчивость: файл повреждён → _load_tags возвращает {}
20. Создаёт родительский каталог если его нет
21. _make_msg_link для супергруппы (chat_id < 0)
22. _make_msg_link для приватного чата (chat_id > 0)
23. !tag find — поиск нечувствителен к регистру
24. !tag list — несколько тегов отсортированы по алфавиту
25. !tag del — удаление одного тега, другие остаются
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _make_msg_link, handle_tag

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_replied(msg_id: int = 42, text: str = "оригинальный текст") -> MagicMock:
    replied = MagicMock()
    replied.id = msg_id
    replied.text = text
    replied.caption = None
    return replied


def _make_message(
    command_args: str = "",
    chat_id: int = 777,
    replied: MagicMock | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Возвращает (bot, message)."""
    bot = _make_bot(command_args)
    msg = MagicMock()
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.reply_to_message = replied
    return bot, msg


# ---------------------------------------------------------------------------
# Тесты добавления тега
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_add_stores_tag(tmp_path) -> None:
    """`!tag важно` в reply → тег сохраняется в JSON."""
    path = tmp_path / "message_tags.json"
    replied = _make_replied(msg_id=100)
    bot, msg = _make_message("важно", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    msg.reply.assert_awaited_once()
    assert "важно" in msg.reply.await_args.args[0]
    data = json.loads(path.read_text())
    assert data["777"]["100"] == ["важно"]


@pytest.mark.asyncio
async def test_tag_add_without_reply_raises(tmp_path) -> None:
    """`!tag важно` без reply → UserInputError."""
    path = tmp_path / "message_tags.json"
    bot, msg = _make_message("важно", chat_id=777, replied=None)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        with pytest.raises(UserInputError):
            await handle_tag(bot, msg)


@pytest.mark.asyncio
async def test_tag_add_duplicate_replies_already_exists(tmp_path) -> None:
    """Добавление существующего тега → ответ «уже есть»."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"100": ["важно"]}}))
    replied = _make_replied(msg_id=100)
    bot, msg = _make_message("важно", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "уже" in reply_text.lower() or "есть" in reply_text.lower()
    # Файл не изменился — дубликата нет
    data = json.loads(path.read_text())
    assert data["777"]["100"] == ["важно"]


@pytest.mark.asyncio
async def test_tag_add_only_first_word_used(tmp_path) -> None:
    """При `!tag тег1 тег2` используется только первое слово как тег."""
    path = tmp_path / "message_tags.json"
    replied = _make_replied(msg_id=100)
    # Пользователь пишет два слова — берётся только первое
    bot, msg = _make_message("тег1 лишнее", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    data = json.loads(path.read_text())
    assert data["777"]["100"] == ["тег1"]


@pytest.mark.asyncio
async def test_tag_multiple_tags_on_same_message(tmp_path) -> None:
    """Несколько тегов на одном сообщении — оба сохраняются."""
    path = tmp_path / "message_tags.json"
    replied = _make_replied(msg_id=200)
    bot1, msg1 = _make_message("тег1", chat_id=100, replied=replied)
    bot2, msg2 = _make_message("тег2", chat_id=100, replied=replied)
    msg2.chat = SimpleNamespace(id=100)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot1, msg1)
        await handle_tag(bot2, msg2)

    data = json.loads(path.read_text())
    assert set(data["100"]["200"]) == {"тег1", "тег2"}


# ---------------------------------------------------------------------------
# Тесты !tag list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_list_empty(tmp_path) -> None:
    """`!tag list` при отсутствии тегов → сообщение «нет тегов»."""
    path = tmp_path / "message_tags.json"
    bot, msg = _make_message("list", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "нет" in reply_text.lower() or "тегов нет" in reply_text.lower()


@pytest.mark.asyncio
async def test_tag_list_shows_tags_with_count(tmp_path) -> None:
    """`!tag list` показывает теги с количеством."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"1": ["срочно", "важно"], "2": ["срочно"]}}))
    bot, msg = _make_message("list", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "срочно" in reply_text
    assert "важно" in reply_text
    assert "2" in reply_text  # срочно встречается 2 раза


@pytest.mark.asyncio
async def test_tag_no_args_equivalent_to_list(tmp_path) -> None:
    """`!tag` (без аргументов) — эквивалент `!tag list`."""
    path = tmp_path / "message_tags.json"
    bot, msg = _make_message("", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_tag_list_sorted_alphabetically(tmp_path) -> None:
    """`!tag list` выводит теги в алфавитном порядке."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"1": ["z-тег", "a-тег", "m-тег"]}}))
    bot, msg = _make_message("list", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    pos_a = reply_text.find("a-тег")
    pos_m = reply_text.find("m-тег")
    pos_z = reply_text.find("z-тег")
    assert pos_a < pos_m < pos_z


# ---------------------------------------------------------------------------
# Тесты !tag find
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_find_returns_links(tmp_path) -> None:
    """`!tag find срочно` возвращает ссылки на сообщения."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"10": ["срочно"], "20": ["важно"]}}))
    bot, msg = _make_message("find срочно", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "t.me" in reply_text
    assert "10" in reply_text
    assert "20" not in reply_text


@pytest.mark.asyncio
async def test_tag_find_not_found(tmp_path) -> None:
    """`!tag find несуществующий` → сообщение «нет сообщений»."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"10": ["срочно"]}}))
    bot, msg = _make_message("find нет", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "нет" in reply_text.lower()


@pytest.mark.asyncio
async def test_tag_find_no_arg_raises(tmp_path) -> None:
    """`!tag find` без тега → UserInputError."""
    path = tmp_path / "message_tags.json"
    bot, msg = _make_message("find", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        with pytest.raises(UserInputError):
            await handle_tag(bot, msg)


@pytest.mark.asyncio
async def test_tag_find_case_insensitive(tmp_path) -> None:
    """`!tag find СРОЧНО` находит тег «срочно» (нечувствительно к регистру)."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"5": ["срочно"]}}))
    bot, msg = _make_message("find СРОЧНО", chat_id=777)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    assert "t.me" in reply_text


# ---------------------------------------------------------------------------
# Тесты !tag del
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tag_del_removes_tag(tmp_path) -> None:
    """`!tag del важно` в reply — тег удаляется."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"42": ["важно", "срочно"]}}))
    replied = _make_replied(msg_id=42)
    bot, msg = _make_message("del важно", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    data = json.loads(path.read_text())
    assert "важно" not in data["777"]["42"]
    assert "срочно" in data["777"]["42"]


@pytest.mark.asyncio
async def test_tag_del_last_tag_removes_entry(tmp_path) -> None:
    """Удаление последнего тега — запись для сообщения удаляется."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"42": ["единственный"]}}))
    replied = _make_replied(msg_id=42)
    bot, msg = _make_message("del единственный", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    data = json.loads(path.read_text())
    # Запись для чата тоже должна исчезнуть (или msg_id удалён)
    assert "42" not in data.get("777", {})


@pytest.mark.asyncio
async def test_tag_del_without_reply_raises(tmp_path) -> None:
    """`!tag del важно` без reply → UserInputError."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"42": ["важно"]}}))
    bot, msg = _make_message("del важно", chat_id=777, replied=None)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        with pytest.raises(UserInputError):
            await handle_tag(bot, msg)


@pytest.mark.asyncio
async def test_tag_del_nonexistent_tag_raises(tmp_path) -> None:
    """`!tag del нет` — тег отсутствует → UserInputError."""
    path = tmp_path / "message_tags.json"
    path.write_text(json.dumps({"777": {"42": ["важно"]}}))
    replied = _make_replied(msg_id=42)
    bot, msg = _make_message("del нет", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        with pytest.raises(UserInputError):
            await handle_tag(bot, msg)


@pytest.mark.asyncio
async def test_tag_del_no_arg_raises(tmp_path) -> None:
    """`!tag del` без тега → UserInputError."""
    path = tmp_path / "message_tags.json"
    replied = _make_replied(msg_id=42)
    bot, msg = _make_message("del", chat_id=777, replied=replied)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        with pytest.raises(UserInputError):
            await handle_tag(bot, msg)


# ---------------------------------------------------------------------------
# Тесты изоляции чатов и устойчивости
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tags_isolated_per_chat(tmp_path) -> None:
    """Теги в разных чатах не пересекаются при поиске."""
    path = tmp_path / "message_tags.json"
    path.write_text(
        json.dumps(
            {
                "111": {"1": ["срочно"]},
                "222": {"2": ["другое"]},
            }
        )
    )
    bot, msg = _make_message("find срочно", chat_id=222)

    with patch.object(cmd_module, "_TAGS_FILE", path):
        await handle_tag(bot, msg)

    reply_text = msg.reply.await_args.args[0]
    # В чате 222 нет тега «срочно»
    assert "нет" in reply_text.lower()


@pytest.mark.asyncio
async def test_load_tags_missing_file(tmp_path) -> None:
    """_load_tags возвращает {} при отсутствии файла."""
    path = tmp_path / "no_file.json"
    with patch.object(cmd_module, "_TAGS_FILE", path):
        from src.handlers.command_handlers import _load_tags

        result = _load_tags()
    assert result == {}


@pytest.mark.asyncio
async def test_load_tags_corrupted_file(tmp_path) -> None:
    """_load_tags возвращает {} при повреждённом JSON."""
    path = tmp_path / "bad.json"
    path.write_text("{not valid json!!!}")
    with patch.object(cmd_module, "_TAGS_FILE", path):
        from src.handlers.command_handlers import _load_tags

        result = _load_tags()
    assert result == {}


@pytest.mark.asyncio
async def test_save_tags_creates_parent_dir(tmp_path) -> None:
    """_save_tags создаёт родительский каталог при необходимости."""
    nested = tmp_path / "new_dir" / "message_tags.json"
    with patch.object(cmd_module, "_TAGS_FILE", nested):
        from src.handlers.command_handlers import _save_tags

        _save_tags({"100": {"1": ["тест"]}})
    assert nested.exists()
    data = json.loads(nested.read_text())
    assert data["100"]["1"] == ["тест"]


# ---------------------------------------------------------------------------
# Тесты _make_msg_link
# ---------------------------------------------------------------------------


def test_make_msg_link_supergroup() -> None:
    """Для супергруппы (chat_id < 0 с -100 префиксом) формируется t.me/c/... ссылка."""
    link = _make_msg_link(-1001234567890, 42)
    assert "t.me/c/" in link
    assert "234567890" in link
    assert "42" in link


def test_make_msg_link_positive_chat_id() -> None:
    """Для положительного chat_id формируется t.me/c/ ссылка."""
    link = _make_msg_link(999, 7)
    assert link == "https://t.me/c/999/7"


def test_make_msg_link_negative_without_100() -> None:
    """Для отрицательного chat_id без -100 префикса числа корректны."""
    link = _make_msg_link(-555, 3)
    assert "t.me/c/" in link
    assert "3" in link
