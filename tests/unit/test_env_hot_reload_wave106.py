# -*- coding: utf-8 -*-
"""Tests for Wave 106: env hot-reload."""

from __future__ import annotations

import os
import signal as _signal
from pathlib import Path

import pytest

from src.core import env_hot_reload
from src.core.env_hot_reload import (
    SAFE_RELOAD_ENV_VARS,
    _parse_dotenv,
    install_sigusr1_handler,
    reload_safe_env,
)


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch):
    """Чистый os.environ — снимаем все whitelisted флаги перед тестом."""
    for var in SAFE_RELOAD_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield monkeypatch


def _write_dotenv(tmp_path: Path, content: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(content, encoding="utf-8")
    return path


def test_whitelist_flag_applied(tmp_path: Path, isolated_env) -> None:
    """Whitelisted флаг обновляется в os.environ; diff содержит (old, new)."""
    path = _write_dotenv(tmp_path, "KRAB_SWARM_PROBE_ENABLED=1\n")
    result = reload_safe_env(dotenv_path=path)
    assert result["ok"] is True
    assert os.environ["KRAB_SWARM_PROBE_ENABLED"] == "1"
    assert "KRAB_SWARM_PROBE_ENABLED" in result["diff"]
    old, new = result["diff"]["KRAB_SWARM_PROBE_ENABLED"]
    assert old is None and new == "1"


def test_unsafe_var_skipped(tmp_path: Path, isolated_env) -> None:
    """Non-whitelisted var (например credential) попадает в skipped, не в os.environ."""
    path = _write_dotenv(
        tmp_path,
        "GEMINI_API_KEY=secret\nKRAB_RATE_LIMIT_ENABLED=1\n",
    )
    # Заведомо снять GEMINI_API_KEY до запуска.
    isolated_env.delenv("GEMINI_API_KEY", raising=False)
    result = reload_safe_env(dotenv_path=path)
    assert "GEMINI_API_KEY" in result["skipped"]
    assert "GEMINI_API_KEY" not in result["diff"]
    # Credential НЕ должна попасть в os.environ через hot-reload.
    assert os.environ.get("GEMINI_API_KEY") is None
    # А whitelisted флаг применён.
    assert os.environ["KRAB_RATE_LIMIT_ENABLED"] == "1"


def test_missing_dotenv_graceful(tmp_path: Path, isolated_env) -> None:
    """Отсутствие .env — не ошибка, ok=True, diff пустой."""
    path = tmp_path / "nonexistent.env"
    result = reload_safe_env(dotenv_path=path)
    assert result["ok"] is True
    assert result["diff"] == {}
    assert result.get("reason") == "dotenv_missing"


def test_unchanged_flag_not_in_diff(tmp_path: Path, isolated_env) -> None:
    """Если значение совпадает с текущим — флаг попадает в unchanged."""
    isolated_env.setenv("KRAB_RATE_LIMIT_ENABLED", "1")
    path = _write_dotenv(tmp_path, "KRAB_RATE_LIMIT_ENABLED=1\n")
    result = reload_safe_env(dotenv_path=path)
    assert "KRAB_RATE_LIMIT_ENABLED" in result["unchanged"]
    assert "KRAB_RATE_LIMIT_ENABLED" not in result["diff"]


def test_diff_format_structure(tmp_path: Path, isolated_env) -> None:
    """Контракт diff: dict[str, [old, new]] — list (JSON-serializable)."""
    isolated_env.setenv("KRAB_DAILY_BUDGET_EUR", "10")
    path = _write_dotenv(tmp_path, "KRAB_DAILY_BUDGET_EUR=25\n")
    result = reload_safe_env(dotenv_path=path)
    assert result["ok"] is True
    entry = result["diff"]["KRAB_DAILY_BUDGET_EUR"]
    assert isinstance(entry, list) and len(entry) == 2
    assert entry == ["10", "25"]


def test_parser_handles_quotes_and_comments(tmp_path: Path) -> None:
    """Парсер: комментарии, кавычки, export, пустые строки игнорируются корректно."""
    path = _write_dotenv(
        tmp_path,
        """# header comment
export KRAB_SWARM_PROBE_ENABLED="1"
KRAB_RATE_LIMIT_ENABLED='0'

# trailing comment
NOT_AN_ASSIGNMENT
=missing_key
""",
    )
    parsed = _parse_dotenv(path)
    assert parsed["KRAB_SWARM_PROBE_ENABLED"] == "1"
    assert parsed["KRAB_RATE_LIMIT_ENABLED"] == "0"
    assert "NOT_AN_ASSIGNMENT" not in parsed


def test_signal_handler_install_returns_bool(isolated_env) -> None:
    """install_sigusr1_handler возвращает True на POSIX и регистрирует handler."""
    if not hasattr(_signal, "SIGUSR1"):
        pytest.skip("SIGUSR1 недоступен на платформе")
    # Сохранить старый handler чтобы восстановить.
    old = _signal.getsignal(_signal.SIGUSR1)
    try:
        ok = install_sigusr1_handler()
        assert ok is True
        new = _signal.getsignal(_signal.SIGUSR1)
        assert callable(new)
        assert new is not old
    finally:
        _signal.signal(_signal.SIGUSR1, old)


def test_dotenv_path_env_override(
    tmp_path: Path,
    isolated_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KRAB_DOTENV_PATH в os.environ переопределяет CWD/.env."""
    path = _write_dotenv(tmp_path, "KRAB_EAR_PROBE_ENABLED=1\n")
    monkeypatch.setenv("KRAB_DOTENV_PATH", str(path))
    result = reload_safe_env()
    assert result["ok"] is True
    assert os.environ["KRAB_EAR_PROBE_ENABLED"] == "1"
    assert result["dotenv_path"] == str(path)


def test_metric_counter_invoked(
    tmp_path: Path,
    isolated_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prometheus counter дёргается при success (если client установлен)."""
    calls: list[tuple[bool]] = []
    monkeypatch.setattr(
        env_hot_reload,
        "_record_metric",
        lambda success: calls.append((success,)),
    )
    path = _write_dotenv(tmp_path, "KRAB_RATE_LIMIT_ENABLED=1\n")
    reload_safe_env(dotenv_path=path)
    assert calls == [(True,)]
