"""Wave 22-A: тесты для CLI subprocess bypass.

Покрывает:
1. Распознавание codex-cli/* моделей
2. Распознавание google-gemini-cli/* моделей
3. Исключение google/* (прямой SDK, не CLI)
4. Стриппинг провайдер-префикса
5. PATH lookup через shutil.which (mock)
6. Успешный subprocess call (mock asyncio.create_subprocess_exec)
7. Таймаут убивает процесс и бросает RuntimeError
8. binary не найден в PATH → RuntimeError
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.integrations.cli_subprocess_bypass import (
    _build_messages_text,
    _resolve_binary,
    _strip_provider_prefix,
    complete_via_cli,
    is_cli_model,
    is_cli_subprocess_enabled,
)

# ---------------------------------------------------------------------------
# 1. Распознавание codex-cli/* моделей
# ---------------------------------------------------------------------------


def test_is_cli_model_recognizes_codex_cli() -> None:
    """codex-cli/gpt-5.5 → (True, 'codex')."""
    is_cli, binary = is_cli_model("codex-cli/gpt-5.5")
    assert is_cli is True
    assert binary == "codex"


def test_is_cli_model_recognizes_codex_cli_default() -> None:
    """codex-cli/default → (True, 'codex')."""
    is_cli, binary = is_cli_model("codex-cli/default")
    assert is_cli is True
    assert binary == "codex"


# ---------------------------------------------------------------------------
# 2. Распознавание google-gemini-cli/* моделей
# ---------------------------------------------------------------------------


def test_is_cli_model_recognizes_gemini_cli() -> None:
    """google-gemini-cli/gemini-3.1-pro-preview → (True, 'gemini')."""
    is_cli, binary = is_cli_model("google-gemini-cli/gemini-3.1-pro-preview")
    assert is_cli is True
    assert binary == "gemini"


# ---------------------------------------------------------------------------
# 3. Исключение google/* (прямой SDK bypass, не CLI)
# ---------------------------------------------------------------------------


def test_is_cli_model_excludes_google_direct() -> None:
    """google/gemini-3-pro-preview → (False, None); обслуживается Wave 18-B."""
    is_cli, binary = is_cli_model("google/gemini-3-pro-preview")
    assert is_cli is False
    assert binary is None


def test_is_cli_model_excludes_openai_direct() -> None:
    """openai/gpt-4o → (False, None)."""
    is_cli, binary = is_cli_model("openai/gpt-4o")
    assert is_cli is False
    assert binary is None


def test_is_cli_model_excludes_empty_model() -> None:
    """Пустая строка → (False, None)."""
    is_cli, binary = is_cli_model("")
    assert is_cli is False
    assert binary is None


# ---------------------------------------------------------------------------
# 4. Стриппинг провайдер-префикса
# ---------------------------------------------------------------------------


def test_strip_provider_prefix_codex() -> None:
    assert _strip_provider_prefix("codex-cli/gpt-5.5") == "gpt-5.5"


def test_strip_provider_prefix_gemini() -> None:
    assert _strip_provider_prefix("google-gemini-cli/gemini-3.1-pro") == "gemini-3.1-pro"


def test_strip_provider_prefix_no_slash() -> None:
    """Нет слэша — возвращает исходную строку."""
    assert _strip_provider_prefix("noprefix") == "noprefix"


# ---------------------------------------------------------------------------
# 5. PATH lookup через shutil.which (mock)
# ---------------------------------------------------------------------------


def test_resolve_binary_uses_path_found() -> None:
    """shutil.which возвращает путь → _resolve_binary возвращает его."""
    with patch(
        "src.integrations.cli_subprocess_bypass.shutil.which", return_value="/usr/local/bin/codex"
    ):
        result = _resolve_binary("codex")
    assert result == "/usr/local/bin/codex"


def test_resolve_binary_uses_path_not_found() -> None:
    """shutil.which возвращает None → _resolve_binary возвращает None."""
    with patch("src.integrations.cli_subprocess_bypass.shutil.which", return_value=None):
        result = _resolve_binary("codex")
    assert result is None


# ---------------------------------------------------------------------------
# 6. Успешный subprocess call (mock asyncio.create_subprocess_exec)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_cli_subprocess_call() -> None:
    """Успешный вызов: stdout → text response."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"Hello from codex", b""))

    with (
        patch(
            "src.integrations.cli_subprocess_bypass.shutil.which",
            return_value="/usr/local/bin/codex",
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
    ):
        result = await complete_via_cli(
            model="codex-cli/gpt-5.5",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert result == "Hello from codex"


@pytest.mark.asyncio
async def test_complete_via_cli_nonzero_exit_still_returns_stdout() -> None:
    """Non-zero returncode не скрывает stdout (codex/gemini могут возвращать warnings)."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"Partial response", b"some warning"))

    with (
        patch(
            "src.integrations.cli_subprocess_bypass.shutil.which",
            return_value="/usr/bin/gemini",
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
    ):
        result = await complete_via_cli(
            model="google-gemini-cli/gemini-3.1-pro",
            messages=[{"role": "user", "content": "Test"}],
        )

    assert result == "Partial response"


# ---------------------------------------------------------------------------
# 7. Таймаут убивает процесс и бросает RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_cli_timeout_kills_proc() -> None:
    """asyncio.TimeoutError → proc.kill() + RuntimeError с 'timeout'."""
    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    async def slow_communicate():
        await asyncio.sleep(9999)
        return b"", b""

    mock_proc.communicate = slow_communicate

    with (
        patch(
            "src.integrations.cli_subprocess_bypass.shutil.which",
            return_value="/usr/local/bin/codex",
        ),
        patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ),
        patch(
            "asyncio.wait_for",
            side_effect=asyncio.TimeoutError(),
        ),
    ):
        with pytest.raises(RuntimeError, match="timeout"):
            await complete_via_cli(
                model="codex-cli/gpt-5.5",
                messages=[{"role": "user", "content": "Test"}],
                timeout_sec=0.01,
            )

    mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# 8. binary не найден в PATH → RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_via_cli_binary_not_found_raises() -> None:
    """shutil.which возвращает None → RuntimeError при вызове complete_via_cli."""
    with patch(
        "src.integrations.cli_subprocess_bypass.shutil.which",
        return_value=None,
    ):
        with pytest.raises(RuntimeError, match="не найден в PATH"):
            await complete_via_cli(
                model="codex-cli/gpt-5.5",
                messages=[{"role": "user", "content": "Test"}],
            )


# ---------------------------------------------------------------------------
# Вспомогательные тесты _build_messages_text
# ---------------------------------------------------------------------------


def test_build_messages_text_basic() -> None:
    """System + user → форматируется правильно."""
    messages = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "Hello"},
    ]
    result = _build_messages_text(messages)
    assert "[Контекст]: You are helpful" in result
    assert "[Пользователь]: Hello" in result


def test_build_messages_text_multimodal_content() -> None:
    """Мультимодальный content (list of dicts) — извлекается текст."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "What is this?"}]},
    ]
    result = _build_messages_text(messages)
    assert "What is this?" in result


def test_build_messages_text_empty_messages() -> None:
    """Пустой список messages → пустая строка."""
    assert _build_messages_text([]) == ""


def test_is_cli_subprocess_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default env → enabled."""
    monkeypatch.delenv("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", raising=False)
    assert is_cli_subprocess_enabled() is True


def test_is_cli_subprocess_enabled_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """KRAB_CLI_SUBPROCESS_BYPASS_ENABLED=0 → disabled."""
    monkeypatch.setenv("KRAB_CLI_SUBPROCESS_BYPASS_ENABLED", "0")
    assert is_cli_subprocess_enabled() is False
