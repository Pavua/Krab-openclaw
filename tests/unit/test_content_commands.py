# -*- coding: utf-8 -*-
"""
Тесты content_commands — Phase 2 Wave 15 (Session 27).

Покрытие: хелперы (_plural_messages, _extract_yt_url, _apply_template_vars,
_load_snippets/_save_snippets, _load_templates/_save_templates) и
базовые ветки основных handlers через stub.
"""

from __future__ import annotations

import json
import re

import pytest

# ---------------------------------------------------------------------------
# Импорт тестируемых функций
# ---------------------------------------------------------------------------
from src.handlers.commands.content_commands import (
    _YT_URL_RE,
    _apply_template_vars,
    _extract_yt_url,
    _load_snippets,
    _load_templates,
    _plural_messages,
    _save_snippets,
    _save_templates,
    _split_text_for_telegram,
)

# ---------------------------------------------------------------------------
# _plural_messages
# ---------------------------------------------------------------------------


class TestPluralMessages:
    def test_one(self):
        assert _plural_messages(1) == "сообщение"

    def test_two(self):
        assert _plural_messages(2) == "сообщения"

    def test_five(self):
        assert _plural_messages(5) == "сообщений"

    def test_eleven(self):
        assert _plural_messages(11) == "сообщений"

    def test_twenty_one(self):
        assert _plural_messages(21) == "сообщение"

    def test_hundred_eleven(self):
        assert _plural_messages(111) == "сообщений"

    def test_zero(self):
        assert _plural_messages(0) == "сообщений"


# ---------------------------------------------------------------------------
# _extract_yt_url
# ---------------------------------------------------------------------------


class TestExtractYtUrl:
    def test_standard_watch(self):
        url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert _extract_yt_url(url) == url

    def test_short_url(self):
        url = "https://youtu.be/dQw4w9WgXcQ"
        assert _extract_yt_url(url) == url

    def test_shorts(self):
        url = "https://youtube.com/shorts/abc123DEF"
        assert _extract_yt_url(url) == url

    def test_embedded_in_text(self):
        text = "Посмотри это: https://youtu.be/xyz789 — классное видео"
        assert _extract_yt_url(text) == "https://youtu.be/xyz789"

    def test_no_url(self):
        assert _extract_yt_url("просто текст без ссылок") is None

    def test_empty_string(self):
        assert _extract_yt_url("") is None

    def test_non_yt_url(self):
        assert _extract_yt_url("https://vimeo.com/12345") is None


# ---------------------------------------------------------------------------
# _apply_template_vars
# ---------------------------------------------------------------------------


class TestApplyTemplateVars:
    def test_single_var(self):
        assert _apply_template_vars("Привет, {name}!", ["Павел"]) == "Привет, Павел!"

    def test_two_vars(self):
        result = _apply_template_vars("{greeting}, {name}!", ["Привет", "Миша"])
        assert result == "Привет, Миша!"

    def test_no_vars(self):
        assert _apply_template_vars("Просто текст", []) == "Просто текст"

    def test_no_args_provided(self):
        # Переменные остаются нетронутыми если аргументов не хватает
        result = _apply_template_vars("Привет, {name}!", [])
        assert result == "Привет, {name}!"

    def test_partial_substitution(self):
        result = _apply_template_vars("{a} и {b} и {c}", ["first", "second"])
        assert result == "first и second и {c}"

    def test_repeated_var_substituted_once(self):
        # Одно и то же имя — один placeholder, один аргумент
        result = _apply_template_vars("{x} {x}", ["yes"])
        # Оба вхождения заменяются (str.replace)
        assert result == "yes yes"


# ---------------------------------------------------------------------------
# _split_text_for_telegram
# ---------------------------------------------------------------------------


class TestSplitTextForTelegram:
    def test_short_text_not_split(self):
        text = "короткий текст"
        assert _split_text_for_telegram(text) == [text]

    def test_exact_limit_not_split(self):
        text = "a" * 4000
        assert _split_text_for_telegram(text) == [text]

    def test_long_text_split(self):
        text = "b" * 8001
        parts = _split_text_for_telegram(text)
        assert len(parts) == 3
        assert all(len(p) <= 4000 for p in parts)
        assert "".join(parts) == text

    def test_custom_limit(self):
        text = "x" * 150
        parts = _split_text_for_telegram(text, limit=100)
        assert len(parts) == 2
        assert parts[0] == "x" * 100
        assert parts[1] == "x" * 50


# ---------------------------------------------------------------------------
# _load_snippets / _save_snippets (disk round-trip)
# ---------------------------------------------------------------------------


class TestSnippetStorage:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        monkeypatch.setattr(cc, "_SNIPPETS_FILE", tmp_path / "no_such.json")
        assert cc._load_snippets() == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        monkeypatch.setattr(cc, "_SNIPPETS_FILE", tmp_path / "snippets.json")
        data = {"hello": {"code": "print('hi')", "created_at": "2026-01-01T00:00:00+00:00"}}
        cc._save_snippets(data)
        loaded = cc._load_snippets()
        assert loaded == data

    def test_load_corrupt_json(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        f = tmp_path / "corrupt.json"
        f.write_text("not json!!!", encoding="utf-8")
        monkeypatch.setattr(cc, "_SNIPPETS_FILE", f)
        # Должен молча вернуть пустой dict, а не упасть
        assert cc._load_snippets() == {}


# ---------------------------------------------------------------------------
# _load_templates / _save_templates (disk round-trip)
# ---------------------------------------------------------------------------


class TestTemplateStorage:
    def test_load_missing_file(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        monkeypatch.setattr(cc, "_TEMPLATES_FILE", tmp_path / "no_such.json")
        assert cc._load_templates() == {}

    def test_save_and_load(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        monkeypatch.setattr(cc, "_TEMPLATES_FILE", tmp_path / "templates.json")
        data = {"greet": "Привет, {name}!"}
        cc._save_templates(data)
        loaded = cc._load_templates()
        assert loaded == data

    def test_load_corrupt_json(self, tmp_path, monkeypatch):
        import src.handlers.commands.content_commands as cc

        f = tmp_path / "bad.json"
        f.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(cc, "_TEMPLATES_FILE", f)
        assert cc._load_templates() == {}


# ---------------------------------------------------------------------------
# Re-export: символы доступны в command_handlers
# ---------------------------------------------------------------------------


class TestReexport:
    def test_handle_yt_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_yt")
        assert callable(ch.handle_yt)

    def test_handle_img_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_img")

    def test_handle_backup_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_backup")
        assert hasattr(ch, "_BACKUP_FILES")
        assert isinstance(ch._BACKUP_FILES, list)
        assert "code_snippets.json" in ch._BACKUP_FILES

    def test_handle_snippet_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_snippet")
        assert hasattr(ch, "_load_snippets")
        assert hasattr(ch, "_save_snippets")

    def test_handle_template_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_template")
        assert hasattr(ch, "_apply_template_vars")

    def test_handle_top_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "handle_top")
        assert hasattr(ch, "_plural_messages")

    def test_yt_helpers_reexported(self):
        from src.handlers import command_handlers as ch

        assert hasattr(ch, "_YT_URL_RE")
        assert hasattr(ch, "_YT_PROMPT_TEMPLATE")
        assert hasattr(ch, "_extract_yt_url")
