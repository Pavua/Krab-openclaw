# -*- coding: utf-8 -*-
"""
Тесты для src/core/subprocess_env.py и src/integrations/cli_runner.py.
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# imports via src.* package
from src.core.subprocess_env import (
    _HOMEBREW_PATH_PREFIXES,
    _MALLOC_DEBUG_KEYS,
    clean_subprocess_env,
)

# ─────────────────────────────────────────────────────────────
# subprocess_env: clean_subprocess_env()
# ─────────────────────────────────────────────────────────────


class TestCleanSubprocessEnv:
    """Тесты фильтрации окружения для subprocess'ов."""

    def test_returns_dict(self):
        """Возвращаемое значение — словарь."""
        result = clean_subprocess_env()
        assert isinstance(result, dict)

    def test_is_copy_not_original(self):
        """Возвращается копия, а не сам os.environ."""
        result = clean_subprocess_env()
        assert result is not os.environ

    def test_malloc_keys_removed(self):
        """Все malloc-ключи удалены из результата."""
        injected = {k: "1" for k in _MALLOC_DEBUG_KEYS}
        with patch.dict(os.environ, injected):
            result = clean_subprocess_env()
        for key in _MALLOC_DEBUG_KEYS:
            assert key not in result, f"{key} должен быть удалён"

    def test_malloc_keys_not_present_if_missing(self):
        """Если malloc-ключей нет в окружении — функция не падает."""
        clean_env = {k: v for k, v in os.environ.items() if k not in _MALLOC_DEBUG_KEYS}
        with patch.dict(os.environ, clean_env, clear=True):
            result = clean_subprocess_env()
        for key in _MALLOC_DEBUG_KEYS:
            assert key not in result

    def test_homebrew_prefixes_added_when_missing(self):
        """Homebrew-пути добавляются, если их нет в PATH."""
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False):
            result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)
        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert prefix in path_entries, f"{prefix} должен быть в PATH"

    def test_homebrew_prefixes_not_duplicated(self):
        """Homebrew-пути не дублируются, если уже присутствуют в PATH."""
        existing = os.pathsep.join(_HOMEBREW_PATH_PREFIXES)
        with patch.dict(os.environ, {"PATH": existing}, clear=False):
            result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)
        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert path_entries.count(prefix) == 1, f"{prefix} не должен дублироваться"

    def test_homebrew_prefixes_prepended(self):
        """Homebrew-пути добавляются в начало PATH, не в конец."""
        original_path = "/usr/bin:/bin"
        with patch.dict(os.environ, {"PATH": original_path}, clear=False):
            result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)
        assert path_entries[0] in _HOMEBREW_PATH_PREFIXES

    def test_empty_path_gets_homebrew_prefixes(self):
        """Если PATH пуст — устанавливаются только homebrew-префиксы."""
        with patch.dict(os.environ, {"PATH": ""}, clear=False):
            result = clean_subprocess_env()
        path_entries = result["PATH"].split(os.pathsep)
        for prefix in _HOMEBREW_PATH_PREFIXES:
            assert prefix in path_entries

    def test_other_env_vars_preserved(self):
        """Прочие переменные окружения сохраняются без изменений."""
        with patch.dict(os.environ, {"MY_CUSTOM_VAR": "hello_krab"}):
            result = clean_subprocess_env()
        assert result.get("MY_CUSTOM_VAR") == "hello_krab"


# ─────────────────────────────────────────────────────────────
# cli_runner: run_cli()
# ─────────────────────────────────────────────────────────────


class TestRunCli:
    """Тесты асинхронного запуска CLI-инструментов."""

    @pytest.mark.asyncio
    async def test_tool_not_found_returns_127(self):
        """Если бинарь не найден в PATH — exit_code 127 и timed_out=False."""
        from src.integrations.cli_runner import run_cli

        with patch("src.integrations.cli_runner.shutil.which", return_value=None):
            result = await run_cli("nonexistent_tool", "hello")

        assert result.exit_code == 127
        assert result.timed_out is False
        assert result.tool == "nonexistent_tool"
        assert "не найден" in result.output

    @pytest.mark.asyncio
    async def test_prompt_preview_truncated_at_80(self):
        """prompt_preview обрезается до 80 символов + '...'."""
        from src.integrations.cli_runner import run_cli

        long_prompt = "x" * 120

        with patch("src.integrations.cli_runner.shutil.which", return_value=None):
            result = await run_cli("tool", long_prompt)

        assert result.prompt_preview == "x" * 80 + "..."

    @pytest.mark.asyncio
    async def test_prompt_preview_short(self):
        """Короткий prompt_preview — без '...'."""
        from src.integrations.cli_runner import run_cli

        with patch("src.integrations.cli_runner.shutil.which", return_value=None):
            result = await run_cli("tool", "short")

        assert result.prompt_preview == "short"

    @pytest.mark.asyncio
    async def test_successful_run_returns_output(self):
        """Успешный запуск возвращает stdout и exit_code=0."""
        from src.integrations.cli_runner import run_cli

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello output", b""))

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/usr/bin/echo"),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
        ):
            result = await run_cli("echo", "hi")

        assert result.exit_code == 0
        assert result.output == "hello output"
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_timeout_sets_flag_and_appends_suffix(self):
        """При таймауте timed_out=True и в output есть предупреждение."""
        from src.integrations.cli_runner import run_cli

        mock_proc = AsyncMock()
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        # Мокаем wait_for так, чтобы он поднял TimeoutError
        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/usr/bin/slow"),
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=raise_timeout),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await run_cli("slow", "prompt", timeout=30)

        assert result.timed_out is True
        assert "Таймаут" in result.output

    @pytest.mark.asyncio
    async def test_oserror_returns_exit_code_1(self):
        """OSError при запуске — exit_code=1 и timed_out=False."""
        from src.integrations.cli_runner import run_cli

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/fake/bin/tool"),
            patch("asyncio.create_subprocess_exec", side_effect=OSError("no such file")),
        ):
            result = await run_cli("tool", "prompt")

        assert result.exit_code == 1
        assert result.timed_out is False
        assert "Ошибка запуска" in result.output

    @pytest.mark.asyncio
    async def test_tool_flags_included_in_command(self):
        """Флаги инструмента из _TOOL_FLAGS передаются в команду."""
        from src.integrations.cli_runner import run_cli

        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            return proc

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/usr/bin/gemini"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await run_cli("gemini", "test prompt")

        assert "-p" in captured_cmd
        assert "test prompt" in captured_cmd

    @pytest.mark.asyncio
    async def test_extra_args_prepended_before_tool_flags(self):
        """extra_args вставляются перед стандартными флагами инструмента."""
        from src.integrations.cli_runner import run_cli

        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", b""))
            return proc

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await run_cli("codex", "prompt", extra_args=["--model", "gpt-4"])

        idx_model = captured_cmd.index("--model")
        idx_q = captured_cmd.index("-q")
        assert idx_model < idx_q

    @pytest.mark.asyncio
    async def test_unknown_tool_has_no_default_flags(self):
        """Неизвестный инструмент запускается без флагов (только prompt)."""
        from src.integrations.cli_runner import run_cli

        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            proc = AsyncMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"done", b""))
            return proc

        with (
            patch("src.integrations.cli_runner.shutil.which", return_value="/usr/local/bin/mytool"),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            await run_cli("mytool", "my prompt")

        # Команда: [bin_path, prompt] — без лишних флагов
        assert captured_cmd[-1] == "my prompt"
        assert len(captured_cmd) == 2  # только bin_path + prompt

    @pytest.mark.asyncio
    async def test_cli_result_fields_populated(self):
        """CliResult содержит все ожидаемые поля."""
        from src.integrations.cli_runner import CliResult, run_cli

        with patch("src.integrations.cli_runner.shutil.which", return_value=None):
            result = await run_cli("notfound", "test")

        assert isinstance(result, CliResult)
        assert hasattr(result, "exit_code")
        assert hasattr(result, "output")
        assert hasattr(result, "timed_out")
        assert hasattr(result, "tool")
        assert hasattr(result, "prompt_preview")
