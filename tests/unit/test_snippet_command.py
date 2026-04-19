# -*- coding: utf-8 -*-
"""
Тесты команды !snippet (handle_snippet) из src/handlers/command_handlers.py.

Покрытие:
1. !snippet save <name> <code> — сохраняет сниппет inline
2. !snippet save <name> в reply — сохраняет текст из reply
3. !snippet save без аргументов → UserInputError
4. !snippet save без кода и без reply → UserInputError
5. !snippet save reply без текста → UserInputError
6. !snippet <name> — показывает сниппет в code block
7. !snippet <name> несуществующий → UserInputError
8. !snippet list — выводит список
9. !snippet list пустой — сообщение об отсутствии
10. !snippet (без аргументов) — эквивалент list
11. !snippet del <name> — удаляет
12. !snippet del несуществующее имя → UserInputError
13. !snippet del без имени → UserInputError
14. !snippet search <query> — поиск по имени
15. !snippet search <query> — поиск по содержимому
16. !snippet search ничего не найдено
17. !snippet search без запроса → UserInputError
18. Сохранение файла идёт в корректный путь
19. created_at присутствует в сохранённом сниппете
20. !snippet save перезаписывает существующий сниппет
21. Имя приводится к нижнему регистру
22. !snippet list показывает сниппеты в алфавитном порядке
23. Устойчивость: файл отсутствует
24. Устойчивость: файл повреждён (невалидный JSON)
25. Создаёт родительский каталог если его нет
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import handle_snippet

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_text_message(text: str) -> MagicMock:
    """Создаёт сообщение с текстом для reply."""
    msg = MagicMock()
    msg.text = text
    msg.caption = None
    return msg


def _make_message(
    command_args: str = "",
    reply_text: str | None = None,
    chat_id: int = 777,
) -> tuple[MagicMock, MagicMock]:
    """Возвращает (bot, message)."""
    bot = _make_bot(command_args)
    msg = MagicMock()
    msg.chat = SimpleNamespace(id=chat_id)
    msg.reply = AsyncMock()
    msg.delete = AsyncMock()
    if reply_text is not None:
        msg.reply_to_message = _make_text_message(reply_text)
    else:
        msg.reply_to_message = None
    return bot, msg


# ---------------------------------------------------------------------------
# Тесты !snippet save (inline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_save_inline_stores_code(tmp_path) -> None:
    """`!snippet save hello print('hello')` — код сохраняется в JSON."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save hello print('hello')")
        await handle_snippet(bot, msg)

    msg.reply.assert_awaited_once()
    assert "hello" in msg.reply.await_args.args[0]
    data = json.loads(path.read_text())
    assert "hello" in data
    assert data["hello"]["code"] == "print('hello')"


@pytest.mark.asyncio
async def test_snippet_save_inline_multiword_code(tmp_path) -> None:
    """`!snippet save foo x = 1 + 2` — код с пробелами сохраняется полностью."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save foo x = 1 + 2")
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert data["foo"]["code"] == "x = 1 + 2"


@pytest.mark.asyncio
async def test_snippet_save_inline_has_created_at(tmp_path) -> None:
    """`!snippet save` создаёт запись с полем created_at."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save ts pass")
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert "created_at" in data["ts"]
    assert data["ts"]["created_at"]  # непустая строка


@pytest.mark.asyncio
async def test_snippet_save_overwrites_existing(tmp_path) -> None:
    """`!snippet save foo` второй раз перезаписывает код."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "OLD", "created_at": "2024-01-01T00:00:00"}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save foo NEW_CODE")
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert data["foo"]["code"] == "NEW_CODE"


@pytest.mark.asyncio
async def test_snippet_save_name_lowercased(tmp_path) -> None:
    """`!snippet save FOO code` — имя приводится к lower."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save FOO mycode")
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert "foo" in data
    assert "FOO" not in data


# ---------------------------------------------------------------------------
# Тесты !snippet save в reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_save_from_reply(tmp_path) -> None:
    """`!snippet save myname` в reply на сообщение → текст reply сохраняется."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save myname", reply_text="x = 42\nprint(x)")
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert data["myname"]["code"] == "x = 42\nprint(x)"


@pytest.mark.asyncio
async def test_snippet_save_caption_fallback(tmp_path) -> None:
    """`!snippet save` — если text=None, берём caption из reply."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save capsnip")
        replied = MagicMock()
        replied.text = None
        replied.caption = "caption_code = True"
        msg.reply_to_message = replied
        await handle_snippet(bot, msg)

    data = json.loads(path.read_text())
    assert data["capsnip"]["code"] == "caption_code = True"


# ---------------------------------------------------------------------------
# Тесты !snippet save — ошибочные случаи
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_save_no_name_raises(tmp_path) -> None:
    """`!snippet save` без имени → UserInputError."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save")
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


@pytest.mark.asyncio
async def test_snippet_save_no_code_no_reply_raises(tmp_path) -> None:
    """`!snippet save foo` без кода и без reply → UserInputError."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save foo")
        # reply_to_message=None (по умолчанию)
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


@pytest.mark.asyncio
async def test_snippet_save_reply_empty_text_raises(tmp_path) -> None:
    """`!snippet save foo` — reply есть, но text=None и caption=None → UserInputError."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save foo")
        replied = MagicMock()
        replied.text = None
        replied.caption = None
        msg.reply_to_message = replied
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


# ---------------------------------------------------------------------------
# Тесты !snippet <name> (показ)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_show_existing(tmp_path) -> None:
    """`!snippet hello` — показывает сниппет в code block."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps({"hello": {"code": "print('hello')", "created_at": "2025-01-15T10:00:00+00:00"}})
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("hello")
        await handle_snippet(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "hello" in text
    assert "print('hello')" in text
    assert "```" in text


@pytest.mark.asyncio
async def test_snippet_show_includes_date(tmp_path) -> None:
    """`!snippet foo` — дата создания присутствует в ответе."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "x=1", "created_at": "2025-03-20T12:00:00+00:00"}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("foo")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "2025-03-20" in text


@pytest.mark.asyncio
async def test_snippet_show_unknown_raises(tmp_path) -> None:
    """`!snippet notexist` → UserInputError."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("notexist")
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


@pytest.mark.asyncio
async def test_snippet_show_case_insensitive(tmp_path) -> None:
    """`!snippet FOO` работает как `!snippet foo`."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "pass", "created_at": ""}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("FOO")
        await handle_snippet(bot, msg)

    msg.reply.assert_awaited_once()
    assert "pass" in msg.reply.await_args.args[0]


# ---------------------------------------------------------------------------
# Тесты !snippet list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_list_shows_names(tmp_path) -> None:
    """`!snippet list` — перечисляет имена."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "alpha": {"code": "a=1", "created_at": ""},
                "beta": {"code": "b=2", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("list")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "alpha" in text
    assert "beta" in text


@pytest.mark.asyncio
async def test_snippet_list_sorted_alphabetically(tmp_path) -> None:
    """`!snippet list` — список в алфавитном порядке."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "zzz": {"code": "z", "created_at": ""},
                "aaa": {"code": "a", "created_at": ""},
                "mmm": {"code": "m", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("list")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    pos_a = text.index("aaa")
    pos_m = text.index("mmm")
    pos_z = text.index("zzz")
    assert pos_a < pos_m < pos_z


@pytest.mark.asyncio
async def test_snippet_list_empty(tmp_path) -> None:
    """`!snippet list` при пустом хранилище → сообщение об отсутствии."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("list")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Нет" in text or "нет" in text or "save" in text.lower()


@pytest.mark.asyncio
async def test_snippet_no_args_is_list(tmp_path) -> None:
    """`!snippet` без аргументов — эквивалент list."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"myfunc": {"code": "def f(): pass", "created_at": ""}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "myfunc" in text


# ---------------------------------------------------------------------------
# Тесты !snippet del
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_del_removes_entry(tmp_path) -> None:
    """`!snippet del foo` → запись удаляется из файла."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "foo": {"code": "foo_code", "created_at": ""},
                "bar": {"code": "bar_code", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("del foo")
        await handle_snippet(bot, msg)

    msg.reply.assert_awaited_once()
    data = json.loads(path.read_text())
    assert "foo" not in data
    assert "bar" in data


@pytest.mark.asyncio
async def test_snippet_del_unknown_raises(tmp_path) -> None:
    """`!snippet del missing` → UserInputError."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "x", "created_at": ""}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("del missing")
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


@pytest.mark.asyncio
async def test_snippet_del_without_name_raises(tmp_path) -> None:
    """`!snippet del` без имени → UserInputError."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "x", "created_at": ""}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("del")
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


# ---------------------------------------------------------------------------
# Тесты !snippet search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_search_by_name(tmp_path) -> None:
    """`!snippet search fetch` — находит сниппеты с 'fetch' в имени."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "fetch_data": {"code": "requests.get(url)", "created_at": ""},
                "parse_json": {"code": "json.loads(s)", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search fetch")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "fetch_data" in text
    assert "parse_json" not in text


@pytest.mark.asyncio
async def test_snippet_search_by_code_content(tmp_path) -> None:
    """`!snippet search requests` — находит сниппеты с 'requests' в коде."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "http_get": {"code": "import requests\nrequests.get(url)", "created_at": ""},
                "math_op": {"code": "result = 1 + 2", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search requests")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "http_get" in text
    assert "math_op" not in text


@pytest.mark.asyncio
async def test_snippet_search_case_insensitive(tmp_path) -> None:
    """`!snippet search IMPORT` — поиск регистронезависимый."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "imports": {"code": "import os\nimport sys", "created_at": ""},
                "unrelated": {"code": "x = 1", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search IMPORT")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "imports" in text


@pytest.mark.asyncio
async def test_snippet_search_not_found(tmp_path) -> None:
    """`!snippet search xyz` — ничего не найдено → сообщение об отсутствии."""
    path = tmp_path / "code_snippets.json"
    path.write_text(json.dumps({"foo": {"code": "bar", "created_at": ""}}))

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search xyz_not_exists")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "Ничего" in text or "не найдено" in text.lower() or "не нашлось" in text.lower()


@pytest.mark.asyncio
async def test_snippet_search_no_query_raises(tmp_path) -> None:
    """`!snippet search` без запроса → UserInputError."""
    path = tmp_path / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search")
        with pytest.raises(UserInputError):
            await handle_snippet(bot, msg)


@pytest.mark.asyncio
async def test_snippet_search_shows_count(tmp_path) -> None:
    """`!snippet search` — ответ содержит количество результатов."""
    path = tmp_path / "code_snippets.json"
    path.write_text(
        json.dumps(
            {
                "aio_get": {"code": "aiohttp.get()", "created_at": ""},
                "aio_post": {"code": "aiohttp.post()", "created_at": ""},
                "sync_fn": {"code": "requests.get()", "created_at": ""},
            }
        )
    )

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("search aio")
        await handle_snippet(bot, msg)

    text = msg.reply.await_args.args[0]
    assert "2" in text  # два совпадения


# ---------------------------------------------------------------------------
# Тесты устойчивости
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snippet_list_no_file(tmp_path) -> None:
    """`!snippet list` если файл не существует → сообщение об отсутствии."""
    path = tmp_path / "code_snippets.json"
    # файл не создаём

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("list")
        await handle_snippet(bot, msg)

    msg.reply.assert_awaited_once()
    text = msg.reply.await_args.args[0]
    assert "Нет" in text or "save" in text.lower()


@pytest.mark.asyncio
async def test_snippet_list_corrupted_file(tmp_path) -> None:
    """`!snippet list` при повреждённом JSON → пустой список (не падает)."""
    path = tmp_path / "code_snippets.json"
    path.write_text("NOT VALID JSON {{{")

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("list")
        await handle_snippet(bot, msg)

    # Функция не должна упасть — даст сообщение "Нет сниппетов"
    msg.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_snippet_save_creates_parent_dir(tmp_path) -> None:
    """`!snippet save` создаёт родительский каталог если его нет."""
    path = tmp_path / "nested" / "deep" / "code_snippets.json"

    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        bot, msg = _make_message("save mysnip x=1")
        await handle_snippet(bot, msg)

    assert path.exists()
    data = json.loads(path.read_text())
    assert "mysnip" in data


# ---------------------------------------------------------------------------
# Тесты чистых функций _load_snippets / _save_snippets
# ---------------------------------------------------------------------------


def test_load_snippets_returns_empty_if_no_file(tmp_path) -> None:
    """_load_snippets возвращает {} если файл не существует."""
    path = tmp_path / "not_exists.json"
    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        result = cmd_module._load_snippets()
    assert result == {}


def test_load_snippets_returns_empty_on_corrupt(tmp_path) -> None:
    """_load_snippets возвращает {} при невалидном JSON."""
    path = tmp_path / "bad.json"
    path.write_text("}{BAD")
    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        result = cmd_module._load_snippets()
    assert result == {}


def test_save_and_load_roundtrip(tmp_path) -> None:
    """_save_snippets + _load_snippets — данные сохраняются корректно."""
    path = tmp_path / "snippets.json"
    data = {"myfunc": {"code": "def f(): pass", "created_at": "2025-01-01T00:00:00+00:00"}}
    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        cmd_module._save_snippets(data)
        loaded = cmd_module._load_snippets()
    assert loaded == data


def test_save_snippets_uses_ensure_ascii_false(tmp_path) -> None:
    """_save_snippets сохраняет кириллицу без экранирования."""
    path = tmp_path / "snippets.json"
    data = {"ru": {"code": "переменная = 42", "created_at": ""}}
    with patch.object(cmd_module, "_SNIPPETS_FILE", path):
        cmd_module._save_snippets(data)
    raw = path.read_text(encoding="utf-8")
    assert "переменная" in raw
    assert "\\u" not in raw
