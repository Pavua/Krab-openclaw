# -*- coding: utf-8 -*-
"""
Wave 11-A regression guard tests: cli_runner._assert_cli_provider_safe.

Защита от re-add telegram MCP в codex/claude/gemini/opencode CLI configs
(Wave 9-B/10-A disabled телеграм MCP exposure там).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.integrations import cli_runner


@pytest.fixture(autouse=True)
def _reset_cache():
    """Очищать idempotency cache перед каждым тестом."""
    cli_runner._cli_safety_checked.clear()
    yield
    cli_runner._cli_safety_checked.clear()


@pytest.fixture
def warn_log(monkeypatch):
    """Перехватывает logger.warning вызовы из cli_runner."""
    captured: list[tuple[str, dict]] = []

    def fake_warning(event, **kw):
        captured.append((event, kw))

    monkeypatch.setattr(cli_runner.logger, "warning", fake_warning)
    return captured


@pytest.fixture
def patched_home(tmp_path, monkeypatch):
    """Подменяем Path.home() на временный каталог."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def _events(log: list, name: str) -> list[dict]:
    return [kw for evt, kw in log if evt == name]


def _write_codex_config(home: Path, body: str) -> Path:
    cfg_dir = home / ".codex"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _write_claude_config(home: Path, payload: dict) -> Path:
    cfg_dir = home / ".claude"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_gemini_config(home: Path, payload: dict) -> Path:
    cfg_dir = home / ".gemini"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "settings.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_codex_disabled_no_warn(patched_home, warn_log):
    body = (
        "# [mcp_servers.krab-telegram]\n"
        '# command = "/path/to/python"\n'
        '# args = ["..."]\n'
        "[mcp_servers.brave]\n"
        'command = "brave-mcp"\n'
    )
    _write_codex_config(patched_home, body)
    cli_runner._assert_cli_provider_safe("codex")
    assert _events(warn_log, "cli_telegram_mcp_active") == []


def test_codex_active_warns(patched_home, warn_log):
    body = '[mcp_servers.krab-telegram]\ncommand = "/path/to/python"\nargs = ["script.py"]\n'
    _write_codex_config(patched_home, body)
    cli_runner._assert_cli_provider_safe("codex")
    events = _events(warn_log, "cli_telegram_mcp_active")
    assert len(events) == 1
    assert events[0]["provider"] == "codex"


def test_claude_disabled_no_warn(patched_home, warn_log):
    _write_claude_config(
        patched_home,
        {"mcpServers": {"brave": {}}, "mcpServers_disabled": {"telegram": {}}},
    )
    cli_runner._assert_cli_provider_safe("claude_cli")
    assert _events(warn_log, "cli_telegram_mcp_active") == []


def test_claude_active_warns(patched_home, warn_log):
    _write_claude_config(
        patched_home,
        {"mcpServers": {"telegram": {"command": "x"}}},
    )
    cli_runner._assert_cli_provider_safe("claude_cli")
    events = _events(warn_log, "cli_telegram_mcp_active")
    assert len(events) == 1
    assert events[0]["provider"] == "claude_cli"


def test_gemini_empty_no_warn(patched_home, warn_log):
    _write_gemini_config(patched_home, {"mcpServers": {}})
    cli_runner._assert_cli_provider_safe("gemini")
    assert _events(warn_log, "cli_telegram_mcp_active") == []


def test_unknown_provider_silent(patched_home, warn_log):
    cli_runner._assert_cli_provider_safe("xyz-unknown")
    cli_runner._assert_cli_provider_safe("cursor")
    assert _events(warn_log, "cli_telegram_mcp_active") == []


def test_assertion_cached(patched_home, warn_log, monkeypatch):
    body = '[mcp_servers.krab-telegram]\ncommand = "x"\n'
    _write_codex_config(patched_home, body)

    calls = {"n": 0}
    real_check = cli_runner._check_codex_telegram_active

    def spy(path):
        calls["n"] += 1
        return real_check(path)

    monkeypatch.setattr(cli_runner, "_check_codex_telegram_active", spy)
    cli_runner._assert_cli_provider_safe("codex")
    cli_runner._assert_cli_provider_safe("codex")
    cli_runner._assert_cli_provider_safe("codex")
    assert calls["n"] == 1, "Проверка должна выполняться только один раз на provider"


def test_idempotent_across_providers(patched_home, warn_log):
    _write_codex_config(
        patched_home,
        '[mcp_servers.krab-telegram]\ncommand = "x"\n',
    )
    _write_claude_config(
        patched_home,
        {"mcpServers": {"telegram": {"command": "y"}}},
    )
    _write_gemini_config(patched_home, {"mcpServers": {}})

    cli_runner._assert_cli_provider_safe("codex")
    cli_runner._assert_cli_provider_safe("claude_cli")
    cli_runner._assert_cli_provider_safe("gemini")

    events = _events(warn_log, "cli_telegram_mcp_active")
    assert len(events) == 2
    providers = {e["provider"] for e in events}
    assert providers == {"codex", "claude_cli"}


def test_malformed_json_no_crash(patched_home, warn_log):
    cfg_dir = patched_home / ".claude"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "settings.json").write_text("{not valid json", encoding="utf-8")
    # Не должно бросать
    cli_runner._assert_cli_provider_safe("claude_cli")
    assert len(_events(warn_log, "cli_provider_safety_read_error")) == 1
    assert _events(warn_log, "cli_telegram_mcp_active") == []


def test_missing_config_silent(patched_home, warn_log):
    cli_runner._assert_cli_provider_safe("codex")
    cli_runner._assert_cli_provider_safe("gemini")
    assert warn_log == []
