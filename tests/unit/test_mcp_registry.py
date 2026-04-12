# -*- coding: utf-8 -*-
"""
Тесты единого MCP-реестра и LM Studio JSON-генератора.
Покрывают: регистрацию серверов, resolve launch, tool manifest merging,
load_project_env, build_lmstudio_mcp_json, helper-функции.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

try:
    from scripts.sync_lmstudio_mcp import _merge_existing_servers

    _HAS_SYNC_SCRIPT = True
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    _HAS_SYNC_SCRIPT = False

try:
    from src.core.mcp_registry import (
        build_lmstudio_mcp_json,
        get_managed_mcp_servers,
        load_project_env,
        resolve_managed_server_launch,
    )

    _HAS_REGISTRY = True
except (ImportError, ModuleNotFoundError, FileNotFoundError):
    _HAS_REGISTRY = False

if not _HAS_REGISTRY:
    pytest.skip("src.core.mcp_registry not available", allow_module_level=True)


# ---------------------------------------------------------------------------
# load_project_env — парсинг .env
# ---------------------------------------------------------------------------


def test_load_project_env_empty_file(tmp_path: Path) -> None:
    """Пустой .env возвращает пустой словарь."""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    assert load_project_env(env_file) == {}


def test_load_project_env_nonexistent_file(tmp_path: Path) -> None:
    """Несуществующий .env возвращает пустой словарь без исключений."""
    assert load_project_env(tmp_path / "nonexistent.env") == {}


def test_load_project_env_parses_key_value(tmp_path: Path) -> None:
    """Базовый парсинг KEY=VALUE."""
    env_file = tmp_path / ".env"
    env_file.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
    result = load_project_env(env_file)
    assert result["FOO"] == "bar"
    assert result["BAZ"] == "qux"


def test_load_project_env_strips_quotes(tmp_path: Path) -> None:
    """Внешние одинарные и двойные кавычки снимаются."""
    env_file = tmp_path / ".env"
    env_file.write_text("KEY1=\"hello world\"\nKEY2='single'\n", encoding="utf-8")
    result = load_project_env(env_file)
    assert result["KEY1"] == "hello world"
    assert result["KEY2"] == "single"


def test_load_project_env_handles_export_prefix(tmp_path: Path) -> None:
    """Строки с `export VAR=val` корректно разбираются."""
    env_file = tmp_path / ".env"
    env_file.write_text("export MY_VAR=abc\n", encoding="utf-8")
    result = load_project_env(env_file)
    assert result["MY_VAR"] == "abc"


def test_load_project_env_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    """Комментарии (#) и пустые строки игнорируются."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        textwrap.dedent("""\
            # это комментарий

            REAL=value
            # ещё комментарий
        """),
        encoding="utf-8",
    )
    result = load_project_env(env_file)
    assert list(result.keys()) == ["REAL"]
    assert result["REAL"] == "value"


# ---------------------------------------------------------------------------
# get_managed_mcp_servers — состав и структура реестра
# ---------------------------------------------------------------------------


def test_get_managed_mcp_servers_returns_nonempty_dict() -> None:
    """Реестр возвращает непустой словарь серверов."""
    servers = get_managed_mcp_servers()
    assert isinstance(servers, dict)
    assert len(servers) > 0


def test_get_managed_mcp_servers_required_entries_present() -> None:
    """Обязательные серверы присутствуют в реестре."""
    servers = get_managed_mcp_servers()
    for name in ("filesystem", "memory", "github", "brave-search", "lmstudio"):
        assert name in servers, f"Сервер '{name}' отсутствует в registry"


def test_get_managed_mcp_servers_each_has_required_fields() -> None:
    """Каждая запись содержит все обязательные поля."""
    servers = get_managed_mcp_servers()
    required_fields = {"description", "command", "args", "env", "required_env", "risk"}
    for name, cfg in servers.items():
        missing = required_fields - cfg.keys()
        assert not missing, f"Сервер '{name}' не имеет полей: {missing}"


def test_get_managed_mcp_servers_risk_values_valid() -> None:
    """Поле risk имеет только допустимые значения."""
    valid_risks = {"low", "medium", "high"}
    for name, cfg in get_managed_mcp_servers().items():
        assert cfg["risk"] in valid_risks, (
            f"Сервер '{name}' имеет недопустимый risk='{cfg['risk']}'"
        )


def test_get_managed_mcp_servers_filesystem_risk_levels() -> None:
    """filesystem — medium risk; filesystem-home — high risk."""
    servers = get_managed_mcp_servers()
    assert servers["filesystem"]["risk"] == "medium"
    assert servers["filesystem-home"]["risk"] == "high"


# ---------------------------------------------------------------------------
# resolve_managed_server_launch — сборка launch-конфига
# ---------------------------------------------------------------------------


def test_resolve_managed_server_launch_unknown_raises_key_error() -> None:
    """Несуществующий сервер вызывает KeyError."""
    with pytest.raises(KeyError):
        resolve_managed_server_launch("nonexistent-server-xyz")


def test_resolve_managed_server_launch_returns_correct_fields() -> None:
    """Известный сервер возвращает словарь с корректными полями."""
    launch = resolve_managed_server_launch("memory")
    assert launch["name"] == "memory"
    assert "command" in launch
    assert isinstance(launch["args"], list)
    assert isinstance(launch["env"], dict)
    assert isinstance(launch["missing_env"], list)


def test_resolve_managed_server_launch_no_required_env_missing_empty() -> None:
    """memory не требует env-ключей — missing_env пустой."""
    launch = resolve_managed_server_launch("memory")
    assert launch["missing_env"] == []


def test_resolve_managed_server_launch_missing_env_detected() -> None:
    """brave-search без BRAVE_SEARCH_API_KEY помечает его в missing_env."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            launch = resolve_managed_server_launch("brave-search")
    assert "BRAVE_SEARCH_API_KEY" in launch["missing_env"]


def test_resolve_managed_server_launch_merges_project_env_and_fixed_env() -> None:
    """Проверяет корректный merge project_env + os.environ + server.env."""
    fake_env = {
        "CONTEXT7_API_KEY": "ctx-demo",
        "LM_STUDIO_URL": "http://192.168.0.171:1234",
        "LM_STUDIO_API_KEY": "lm-demo-token",
        "BRAVE_API_KEY": "brave-legacy",
        "GITHUB_PERSONAL_ACCESS_TOKEN": "gh-legacy",
    }
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            context7 = resolve_managed_server_launch("context7")
            lmstudio = resolve_managed_server_launch("lmstudio")
            brave = resolve_managed_server_launch("brave-search")
            github = resolve_managed_server_launch("github")

    assert context7["missing_env"] == []
    assert context7["env"]["CONTEXT7_API_KEY"] == "ctx-demo"
    assert brave["env"]["BRAVE_SEARCH_API_KEY"] == "brave-legacy"
    assert github["env"]["GITHUB_TOKEN"] == "gh-legacy"
    assert lmstudio["missing_env"] == []
    assert lmstudio["env"]["OPENAI_API_KEY"] == "lm-demo-token"
    assert lmstudio["env"]["OPENAI_BASE_URL"] == "http://192.168.0.171:1234/v1"


# ---------------------------------------------------------------------------
# build_lmstudio_mcp_json — генерация mcp.json
# ---------------------------------------------------------------------------


def test_build_lmstudio_mcp_json_skips_missing_optional_and_high_risk() -> None:
    """Без ключей и с include_high_risk=False опасные/незаполненные серверы пропускаются."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=False,
                include_high_risk=False,
            )

    servers = payload["mcpServers"]
    assert "filesystem" in servers
    assert "lmstudio" in servers
    assert "openclaw-browser" in servers
    assert "chrome-profile" in servers
    assert "context7" not in servers
    assert "github" not in servers
    assert "firecrawl" not in servers
    assert "shell" not in servers
    assert "filesystem-home" not in servers
    assert "context7" in summary["skipped_missing"]
    assert "shell" in summary["skipped_risk"]


def test_build_lmstudio_mcp_json_include_optional_missing_empties_skipped() -> None:
    """include_optional_missing=True — skipped_missing становится пустым."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            _, summary = build_lmstudio_mcp_json(
                include_optional_missing=True,
                include_high_risk=True,
            )
    assert summary["skipped_missing"] == []


def test_build_lmstudio_mcp_json_summary_has_managed_names() -> None:
    """summary['managed_names'] совпадает с сортированным списком get_managed_mcp_servers."""
    _, summary = build_lmstudio_mcp_json()
    expected = sorted(get_managed_mcp_servers().keys())
    assert summary["managed_names"] == expected


# ---------------------------------------------------------------------------
# _merge_existing_servers — tool manifest merging (только если доступен)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_SYNC_SCRIPT, reason="scripts.sync_lmstudio_mcp недоступен")
def test_merge_existing_servers_drops_managed_names_excluded_by_mode() -> None:
    """Managed-серверы, не попавшие в новый набор, удаляются; кастомные сохраняются."""
    existing = {
        "mcpServers": {
            "shell": {"command": "npx"},
            "xcodebuild": {"command": "npx"},
        }
    }

    merged, preserved = _merge_existing_servers(
        existing,
        {"filesystem": {"command": "python"}},
        ["filesystem", "shell"],
    )

    assert "filesystem" in merged["mcpServers"]
    assert "shell" not in merged["mcpServers"]
    assert "xcodebuild" in merged["mcpServers"]
    assert preserved == ["xcodebuild"]
