# -*- coding: utf-8 -*-
"""
Тесты для src/core/openclaw_workspace.py.

Покрывает:
- resolve_main_workspace_dir: дефолтный путь, кастомный Path, атрибут config
- load_workspace_prompt_bundle: сборка секций, trim по max_chars, recent memory, empty workspace
- append_workspace_memory_entry: новый файл с заголовком, дозапись в существующий,
  пустой текст возвращает False, формат строки с author
- recall_workspace_memory: совпадение по токенам, нет совпадений, trim по max_chars,
  чтение MEMORY.md и memory/
- build_workspace_state_snapshot: поля ok/exists/prompt_files/memory_file_count/last_memory_entry
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.core.openclaw_workspace import (
    append_workspace_memory_entry,
    build_workspace_state_snapshot,
    load_workspace_prompt_bundle,
    recall_workspace_memory,
    resolve_main_workspace_dir,
)

# ---------------------------------------------------------------------------
# resolve_main_workspace_dir
# ---------------------------------------------------------------------------


class TestResolveMainWorkspaceDir:
    """Проверяем выбор пути workspace."""

    def test_default_points_to_home_openclaw(self):
        """Без аргументов возвращает ~/.openclaw/workspace-main-messaging."""
        result = resolve_main_workspace_dir()
        assert result == Path.home() / ".openclaw" / "workspace-main-messaging"

    def test_explicit_path_returned_as_is(self, tmp_path):
        """При передаче явного Path возвращает его же."""
        result = resolve_main_workspace_dir(tmp_path)
        assert result == tmp_path

    def test_config_attribute_used_when_no_arg(self, tmp_path):
        """Если config.OPENCLAW_MAIN_WORKSPACE_DIR задан, используем его."""
        from src import config as config_module
        with patch.object(config_module.config, "OPENCLAW_MAIN_WORKSPACE_DIR", tmp_path):
            result = resolve_main_workspace_dir()
        assert result == tmp_path


# ---------------------------------------------------------------------------
# load_workspace_prompt_bundle
# ---------------------------------------------------------------------------


class TestLoadWorkspacePromptBundle:
    """Проверяем сборку prompt-bundle из файлов workspace."""

    def test_empty_workspace_returns_empty_string(self, tmp_path):
        """Если workspace пустой (нет файлов), возвращается пустая строка."""
        bundle = load_workspace_prompt_bundle(workspace_dir=tmp_path)
        assert bundle == ""

    def test_reads_soul_and_user_files(self, tmp_path):
        """SOUL.md и USER.md включаются в bundle с секционными заголовками."""
        (tmp_path / "SOUL.md").write_text("I am Krab", encoding="utf-8")
        (tmp_path / "USER.md").write_text("Owner prefs", encoding="utf-8")

        bundle = load_workspace_prompt_bundle(
            workspace_dir=tmp_path,
            include_recent_memory_days=0,
        )

        assert "[SOUL.md]" in bundle
        assert "I am Krab" in bundle
        assert "[USER.md]" in bundle
        assert "Owner prefs" in bundle

    def test_truncates_large_file(self, tmp_path):
        """Файл длиннее max_chars_per_file усекается с суффиксом trimmed."""
        (tmp_path / "TOOLS.md").write_text("T" * 3000, encoding="utf-8")

        bundle = load_workspace_prompt_bundle(
            workspace_dir=tmp_path,
            max_chars_per_file=100,
            include_recent_memory_days=0,
        )

        assert "[...trimmed...]" in bundle

    def test_recent_memory_tail_included(self, tmp_path):
        """При include_recent_memory_days=1 последние строки сегодняшней памяти включаются."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        today = datetime.now().date().isoformat()
        long_line = "старый шум " + ("z" * 400)
        (memory_dir / f"{today}.md").write_text(
            f"# Memory {today}\n\n"
            f"- 08:00 [userbot] {long_line}\n"
            "- 23:55 [userbot] свежий факт Krab\n",
            encoding="utf-8",
        )

        bundle = load_workspace_prompt_bundle(
            workspace_dir=tmp_path,
            max_chars_per_file=150,
            include_recent_memory_days=1,
        )

        assert "свежий факт Krab" in bundle
        # длинная старая строка обрезается
        assert "старый шум" not in bundle

    def test_no_recent_memory_when_days_zero(self, tmp_path):
        """При include_recent_memory_days=0 memory-файлы не читаются."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        today = datetime.now().date().isoformat()
        (memory_dir / f"{today}.md").write_text("- 12:00 [userbot] secret", encoding="utf-8")

        bundle = load_workspace_prompt_bundle(
            workspace_dir=tmp_path,
            include_recent_memory_days=0,
        )

        assert "secret" not in bundle


# ---------------------------------------------------------------------------
# append_workspace_memory_entry
# ---------------------------------------------------------------------------


class TestAppendWorkspaceMemoryEntry:
    """Проверяем запись в дневной markdown-файл памяти."""

    def test_creates_new_file_with_header(self, tmp_path):
        """Новый файл создаётся с заголовком # Memory YYYY-MM-DD."""
        result = append_workspace_memory_entry("факт 1", workspace_dir=tmp_path, source="userbot")
        assert result is True

        memory_dir = tmp_path / "memory"
        today = datetime.now().date().isoformat()
        day_file = memory_dir / f"{today}.md"
        assert day_file.exists()
        content = day_file.read_text(encoding="utf-8")
        assert f"# Memory {today}" in content
        assert "факт 1" in content

    def test_appends_to_existing_file(self, tmp_path):
        """При повторном вызове строка добавляется в конец существующего файла."""
        append_workspace_memory_entry("факт A", workspace_dir=tmp_path, source="userbot")
        append_workspace_memory_entry("факт B", workspace_dir=tmp_path, source="userbot")

        today = datetime.now().date().isoformat()
        content = (tmp_path / "memory" / f"{today}.md").read_text(encoding="utf-8")
        assert "факт A" in content
        assert "факт B" in content

    def test_empty_text_returns_false(self, tmp_path):
        """Пустой текст не пишется, функция возвращает False."""
        result = append_workspace_memory_entry("  ", workspace_dir=tmp_path)
        assert result is False

    def test_author_suffix_in_line(self, tmp_path):
        """При указании author строка содержит [source:author]."""
        append_workspace_memory_entry(
            "тест автора", workspace_dir=tmp_path, source="userbot", author="po"
        )
        today = datetime.now().date().isoformat()
        content = (tmp_path / "memory" / f"{today}.md").read_text(encoding="utf-8")
        assert "[userbot:po]" in content

    def test_line_format_matches_pattern(self, tmp_path):
        """Формат строки соответствует паттерну парсера _MEMORY_LINE_PATTERN."""
        append_workspace_memory_entry("проверка формата", workspace_dir=tmp_path, source="test-src")

        today = datetime.now().date().isoformat()
        lines = (tmp_path / "memory" / f"{today}.md").read_text(encoding="utf-8").splitlines()
        entry_lines = [ln for ln in lines if ln.startswith("- ")]
        assert len(entry_lines) >= 1
        # Проверяем что строка парсится паттерном модуля
        pattern = re.compile(
            r"^- (?P<time>\d{2}:\d{2}) \[(?P<source>[^\]:]+)(?::(?P<author>[^\]]+))?\] (?P<text>.+)$"
        )
        assert pattern.match(entry_lines[0]), f"Строка не соответствует паттерну: {entry_lines[0]}"


# ---------------------------------------------------------------------------
# recall_workspace_memory
# ---------------------------------------------------------------------------


class TestRecallWorkspaceMemory:
    """Проверяем токенный поиск по памяти workspace."""

    def test_finds_matching_line(self, tmp_path):
        """Строка с нужным словом возвращается в результате."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-04-09.md").write_text(
            "# Memory 2026-04-09\n\n"
            "- 11:00 [userbot] GPT-5.4 добавлен в routing\n"
            "- 12:00 [userbot] другая запись без ключевого слова\n",
            encoding="utf-8",
        )

        result = recall_workspace_memory("GPT", workspace_dir=tmp_path)
        assert "GPT-5.4" in result

    def test_empty_query_returns_empty(self, tmp_path):
        """Пустой запрос (нет токенов) возвращает пустую строку."""
        result = recall_workspace_memory("", workspace_dir=tmp_path)
        assert result == ""

    def test_no_match_returns_empty(self, tmp_path):
        """Если нет совпадений — пустая строка."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-04-09.md").write_text(
            "- 09:00 [userbot] ничего нужного здесь нет\n",
            encoding="utf-8",
        )

        result = recall_workspace_memory("XYZNonexistent", workspace_dir=tmp_path)
        assert result == ""

    def test_also_searches_memory_md(self, tmp_path):
        """MEMORY.md в корне workspace тоже участвует в поиске."""
        (tmp_path / "MEMORY.md").write_text(
            "- secret_fact про архитектуру Краба\n",
            encoding="utf-8",
        )

        result = recall_workspace_memory("secret_fact", workspace_dir=tmp_path)
        assert "secret_fact" in result

    def test_respects_max_results(self, tmp_path):
        """max_results ограничивает количество строк в выдаче."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        lines = "\n".join(f"- {i:02d}:00 [userbot] keyword запись {i}" for i in range(10))
        (memory_dir / "2026-04-09.md").write_text(lines, encoding="utf-8")

        result = recall_workspace_memory("keyword", workspace_dir=tmp_path, max_results=3)
        result_lines = [ln for ln in result.splitlines() if ln.strip()]
        assert len(result_lines) <= 3


# ---------------------------------------------------------------------------
# build_workspace_state_snapshot
# ---------------------------------------------------------------------------


class TestBuildWorkspaceStateSnapshot:
    """Проверяем machine-readable snapshot workspace."""

    def test_missing_workspace_still_returns_ok(self, tmp_path):
        """Несуществующий workspace возвращает ok=True, exists=False."""
        missing = tmp_path / "no_workspace"
        snapshot = build_workspace_state_snapshot(workspace_dir=missing)
        assert snapshot["ok"] is True
        assert snapshot["exists"] is False
        assert snapshot["shared_workspace_attached"] is False

    def test_full_workspace_attached(self, tmp_path):
        """При наличии SOUL.md workspace считается прикреплённым."""
        (tmp_path / "SOUL.md").write_text("soul content", encoding="utf-8")
        snapshot = build_workspace_state_snapshot(workspace_dir=tmp_path)
        assert snapshot["shared_workspace_attached"] is True
        assert snapshot["prompt_files"]["SOUL.md"]["exists"] is True

    def test_memory_file_count(self, tmp_path):
        """memory_file_count равен числу .md файлов в memory/."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        for date in ("2026-04-07", "2026-04-08", "2026-04-09"):
            (memory_dir / f"{date}.md").write_text(
                f"- 10:00 [userbot] запись {date}\n", encoding="utf-8"
            )

        snapshot = build_workspace_state_snapshot(workspace_dir=tmp_path)
        assert snapshot["memory_file_count"] == 3

    def test_last_memory_entry_populated(self, tmp_path):
        """last_memory_entry содержит source последней записи."""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-04-09.md").write_text(
            "# Memory 2026-04-09\n\n- 15:00 [reserve-e2e] roundtrip=ok\n",
            encoding="utf-8",
        )

        snapshot = build_workspace_state_snapshot(workspace_dir=tmp_path)
        assert snapshot["last_memory_entry"]["source"] == "reserve-e2e"
        assert snapshot["recent_memory_entries_count"] >= 1

    def test_no_memory_entries_empty_last(self, tmp_path):
        """Без записей памяти last_memory_entry — пустой dict."""
        snapshot = build_workspace_state_snapshot(workspace_dir=tmp_path)
        assert snapshot["last_memory_entry"] == {}
