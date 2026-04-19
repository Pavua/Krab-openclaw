# -*- coding: utf-8 -*-
"""
Тесты команды !template (handle_template) из src/handlers/command_handlers.py.

Покрытие:
1.  _apply_template_vars — без переменных
2.  _apply_template_vars — одна переменная
3.  _apply_template_vars — несколько переменных
4.  _apply_template_vars — переменных больше, чем аргументов (незаполненные остаются)
5.  _apply_template_vars — аргументов больше, чем переменных (лишние игнорируются)
6.  _apply_template_vars — повторяющийся плейсхолдер заменяется один раз
7.  !template save <name> <text> — сохраняет шаблон
8.  !template save с переменными — подсказка содержит имена переменных
9.  !template save перезаписывает существующий шаблон
10. !template save без аргументов -> UserInputError
11. !template save имя без текста -> UserInputError
12. !template save только "save" без имени -> UserInputError (name_and_text пустой)
13. !template <name> — отправляет шаблон без подстановки
14. !template <name> val1 val2 — подставляет аргументы
15. !template <name> несуществующий -> UserInputError
16. !template list — выводит список шаблонов
17. !template list пустой — сообщение об отсутствии
18. !template (без аргументов) — эквивалент list
19. !template del <name> — удаляет шаблон
20. !template del несуществующее имя -> UserInputError
21. !template del без имени -> UserInputError
22. Имя шаблона приводится к нижнему регистру
23. Файл сохраняется по корректному пути
24. Устойчивость: файл отсутствует (_load_templates возвращает {})
25. Устойчивость: файл повреждён — невалидный JSON (_load_templates возвращает {})
26. Создаёт родительский каталог если его нет
27. !template list показывает превью текста (обрезка на 60 символах)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.handlers.command_handlers as cmd_module
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import (
    _apply_template_vars,
    _load_templates,
    _save_templates,
    handle_template,
)

# ---------------------------------------------------------------------------
# Вспомогательные фабрики
# ---------------------------------------------------------------------------


def _make_bot(command_args: str = "") -> MagicMock:
    bot = MagicMock()
    bot.client = MagicMock()
    bot._get_command_args = MagicMock(return_value=command_args)
    return bot


def _make_message() -> AsyncMock:
    msg = AsyncMock()
    msg.reply = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# 1-6: _apply_template_vars (чистые функции — без моков)
# ---------------------------------------------------------------------------


def test_apply_vars_no_placeholders():
    """Текст без плейсхолдеров возвращается как есть."""
    assert _apply_template_vars("Привет!", ["Павел"]) == "Привет!"


def test_apply_vars_single():
    """Один плейсхолдер заменяется на первый аргумент."""
    assert _apply_template_vars("Привет, {name}!", ["Павел"]) == "Привет, Павел!"


def test_apply_vars_multiple():
    """Несколько плейсхолдеров заменяются по порядку."""
    result = _apply_template_vars("Привет, {name}! Тебе {age} лет.", ["Павел", "30"])
    assert result == "Привет, Павел! Тебе 30 лет."


def test_apply_vars_fewer_args():
    """Если аргументов меньше чем переменных — незаполненные остаются."""
    result = _apply_template_vars("{a} и {b}", ["x"])
    assert result == "x и {b}"


def test_apply_vars_more_args():
    """Лишние аргументы просто игнорируются."""
    result = _apply_template_vars("{a}", ["x", "y", "z"])
    assert result == "x"


def test_apply_vars_repeated_placeholder():
    """Повторяющийся плейсхолдер считается одной переменной."""
    # {name} встречается дважды, но в placeholders он один
    result = _apply_template_vars("{name} и {name}", ["Павел"])
    assert result == "Павел и Павел"


# ---------------------------------------------------------------------------
# 7-12: !template save
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_basic(tmp_path, monkeypatch):
    """!template save greet Привет! — сохраняет шаблон без переменных."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save greet Привет!")
    msg = _make_message()

    await handle_template(bot, msg)

    data = json.loads(templates_file.read_text())
    assert data["greet"] == "Привет!"
    msg.reply.assert_awaited_once()
    assert "greet" in msg.reply.call_args[0][0]


@pytest.mark.asyncio
async def test_save_with_vars_hint(tmp_path, monkeypatch):
    """При сохранении шаблона с переменными ответ содержит имена переменных."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save greet Привет, {name}! Как {mood}?")
    msg = _make_message()

    await handle_template(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "{name}" in reply_text
    assert "{mood}" in reply_text


@pytest.mark.asyncio
async def test_save_overwrites(tmp_path, monkeypatch):
    """!template save перезаписывает существующий шаблон."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"greet": "старый"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save greet новый")
    msg = _make_message()
    await handle_template(bot, msg)

    data = json.loads(templates_file.read_text())
    assert data["greet"] == "новый"


@pytest.mark.asyncio
async def test_save_no_args(tmp_path, monkeypatch):
    """!template save без имени и текста -> UserInputError."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save")
    msg = _make_message()

    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


@pytest.mark.asyncio
async def test_save_name_without_text(tmp_path, monkeypatch):
    """!template save greet без текста -> UserInputError."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save greet")
    msg = _make_message()

    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


@pytest.mark.asyncio
async def test_save_empty_name(tmp_path, monkeypatch):
    """!template save с пустым именем -> UserInputError (покрываем ветку empty name)."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    # Передаём " " как имя — после strip() будет пустая строка
    bot = _make_bot("save  текст")
    msg = _make_message()

    # name_and_text[0].strip() == "" → UserInputError
    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


# ---------------------------------------------------------------------------
# 13-15: !template <name> / send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_no_vars(tmp_path, monkeypatch):
    """!template greet — отправляет шаблон без подстановки."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"greet": "Привет!"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("greet")
    msg = _make_message()
    await handle_template(bot, msg)

    msg.reply.assert_awaited_once_with("Привет!")


@pytest.mark.asyncio
async def test_send_with_vars(tmp_path, monkeypatch):
    """!template greet Павел — подставляет переменную {name}."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"greet": "Привет, {name}! Как дела?"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("greet Павел")
    msg = _make_message()
    await handle_template(bot, msg)

    msg.reply.assert_awaited_once_with("Привет, Павел! Как дела?")


@pytest.mark.asyncio
async def test_send_unknown_name(tmp_path, monkeypatch):
    """!template unknown -> UserInputError."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("unknown")
    msg = _make_message()

    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


# ---------------------------------------------------------------------------
# 16-18: !template list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_shows_templates(tmp_path, monkeypatch):
    """!template list — выводит имена шаблонов."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"greet": "Привет!", "bye": "Пока!"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("list")
    msg = _make_message()
    await handle_template(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "greet" in reply_text
    assert "bye" in reply_text


@pytest.mark.asyncio
async def test_list_empty(tmp_path, monkeypatch):
    """!template list при пустом хранилище — сообщение об отсутствии шаблонов."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("list")
    msg = _make_message()
    await handle_template(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "Нет" in reply_text or "нет" in reply_text


@pytest.mark.asyncio
async def test_no_args_is_list(tmp_path, monkeypatch):
    """!template без аргументов — эквивалент list."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"x": "текст"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("")
    msg = _make_message()
    await handle_template(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "x" in reply_text


# ---------------------------------------------------------------------------
# 19-21: !template del
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_del_existing(tmp_path, monkeypatch):
    """!template del greet — удаляет шаблон."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"greet": "Привет!"}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("del greet")
    msg = _make_message()
    await handle_template(bot, msg)

    data = json.loads(templates_file.read_text())
    assert "greet" not in data
    reply_text = msg.reply.call_args[0][0]
    assert "greet" in reply_text


@pytest.mark.asyncio
async def test_del_unknown(tmp_path, monkeypatch):
    """!template del unknown -> UserInputError."""
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("del unknown")
    msg = _make_message()

    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


@pytest.mark.asyncio
async def test_del_no_name(tmp_path, monkeypatch):
    """!template del без имени -> UserInputError."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("del")
    msg = _make_message()

    with pytest.raises(UserInputError):
        await handle_template(bot, msg)


# ---------------------------------------------------------------------------
# 22-27: различные аспекты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_name_lowercased(tmp_path, monkeypatch):
    """Имя шаблона приводится к нижнему регистру при сохранении."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save GREET Привет!")
    msg = _make_message()
    await handle_template(bot, msg)

    data = json.loads(templates_file.read_text())
    assert "greet" in data
    assert "GREET" not in data


@pytest.mark.asyncio
async def test_file_path_correct(tmp_path, monkeypatch):
    """Файл сохраняется по корректному пути (_TEMPLATES_FILE)."""
    templates_file = tmp_path / "message_templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("save t текст")
    msg = _make_message()
    await handle_template(bot, msg)

    assert templates_file.exists()


def test_load_missing_file(tmp_path, monkeypatch):
    """_load_templates возвращает {} если файл отсутствует."""
    missing = tmp_path / "nonexistent.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", missing)
    assert _load_templates() == {}


def test_load_corrupted_file(tmp_path, monkeypatch):
    """_load_templates возвращает {} при невалидном JSON."""
    bad_file = tmp_path / "bad.json"
    bad_file.write_text("не JSON!!!", encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", bad_file)
    assert _load_templates() == {}


def test_save_creates_parent_dir(tmp_path, monkeypatch):
    """_save_templates создаёт родительский каталог если его нет."""
    nested = tmp_path / "deep" / "path" / "templates.json"
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", nested)
    _save_templates({"a": "b"})
    assert nested.exists()
    data = json.loads(nested.read_text())
    assert data["a"] == "b"


@pytest.mark.asyncio
async def test_list_preview_truncated(tmp_path, monkeypatch):
    """!template list обрезает длинный текст до 60 символов с '…'."""
    long_text = "А" * 80
    templates_file = tmp_path / "message_templates.json"
    templates_file.write_text(json.dumps({"long": long_text}), encoding="utf-8")
    monkeypatch.setattr(cmd_module, "_TEMPLATES_FILE", templates_file)

    bot = _make_bot("list")
    msg = _make_message()
    await handle_template(bot, msg)

    reply_text = msg.reply.call_args[0][0]
    assert "…" in reply_text
