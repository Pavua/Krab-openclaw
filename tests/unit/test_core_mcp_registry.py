# -*- coding: utf-8 -*-
"""
Расширенные тесты src/core/mcp_registry.py.

Покрывает: resolve_managed_server_launch, build_lmstudio_mcp_json,
load_project_env, вспомогательные helpers (_lmstudio_api_base_url, etc.).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

try:
    from src.core.mcp_registry import (
        build_lmstudio_mcp_json,
        load_project_env,
        resolve_managed_server_launch,
    )
except (ImportError, ModuleNotFoundError):
    pytest.skip("src.core.mcp_registry not available", allow_module_level=True)


# ──────────────────────────────────────────────────────────────────────────────
# load_project_env
# ──────────────────────────────────────────────────────────────────────────────


def test_load_project_env_missing_file_returns_empty(tmp_path: pytest.fixture) -> None:
    """Несуществующий .env возвращает пустой dict."""
    result = load_project_env(tmp_path / "nonexistent.env")
    assert result == {}


def test_load_project_env_parses_basic_key_value(tmp_path: pytest.fixture) -> None:
    """Стандартные KEY=VALUE пары должны корректно парситься."""
    env_file = tmp_path / ".env"
    env_file.write_text('FOO=bar\nBAZ="quoted"\n', encoding="utf-8")
    result = load_project_env(env_file)
    assert result["FOO"] == "bar"
    assert result["BAZ"] == "quoted"


def test_load_project_env_skips_comments_and_empty_lines(tmp_path: pytest.fixture) -> None:
    """Комментарии и пустые строки игнорируются."""
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nKEY=val\n", encoding="utf-8")
    result = load_project_env(env_file)
    assert "# comment" not in result
    assert result.get("KEY") == "val"


def test_load_project_env_strips_export_prefix(tmp_path: pytest.fixture) -> None:
    """Строки с `export` должны парситься без префикса."""
    env_file = tmp_path / ".env"
    env_file.write_text("export MY_VAR=hello\n", encoding="utf-8")
    result = load_project_env(env_file)
    assert result.get("MY_VAR") == "hello"


# ──────────────────────────────────────────────────────────────────────────────
# resolve_managed_server_launch
# ──────────────────────────────────────────────────────────────────────────────


def test_resolve_managed_server_launch_raises_for_unknown_server() -> None:
    """Запрос несуществующего сервера должен бросать KeyError."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            with pytest.raises(KeyError):
                resolve_managed_server_launch("no_such_server_xyz")


def test_resolve_managed_server_launch_filesystem_has_no_missing_env() -> None:
    """filesystem сервер не требует env-переменных."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            launch = resolve_managed_server_launch("filesystem")
    assert launch["missing_env"] == []
    assert launch["name"] == "filesystem"
    assert launch["risk"] == "medium"


def test_resolve_managed_server_launch_context7_missing_without_key() -> None:
    """context7 должен иметь CONTEXT7_API_KEY в missing_env если ключ не задан."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            launch = resolve_managed_server_launch("context7")
    assert "CONTEXT7_API_KEY" in launch["missing_env"]


def test_resolve_managed_server_launch_lmstudio_base_url_normalization() -> None:
    """LM Studio URL без `/v1` должен нормализоваться."""
    fake_env = {"LM_STUDIO_URL": "http://localhost:1234"}
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            launch = resolve_managed_server_launch("lmstudio")
    assert launch["env"]["OPENAI_BASE_URL"].endswith("/v1")


def test_resolve_managed_server_launch_lmstudio_url_already_v1() -> None:
    """LM Studio URL с `/v1` не должен дублироваться."""
    fake_env = {"LM_STUDIO_URL": "http://localhost:1234/v1"}
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            launch = resolve_managed_server_launch("lmstudio")
    url = launch["env"]["OPENAI_BASE_URL"]
    assert url == "http://localhost:1234/v1"
    assert not url.endswith("/v1/v1")


def test_resolve_managed_server_launch_shell_has_high_risk() -> None:
    """shell сервер должен иметь risk=high."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            launch = resolve_managed_server_launch("shell")
    assert launch["risk"] == "high"


def test_resolve_managed_server_launch_returns_env_as_strings() -> None:
    """Все значения env в launch-конфигурации должны быть строками."""
    fake_env = {"GITHUB_TOKEN": "ghp_test123"}
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            launch = resolve_managed_server_launch("github")
    for key, val in launch["env"].items():
        assert isinstance(val, str), f"Ключ {key!r} должен быть str, получен {type(val).__name__}"


def test_resolve_managed_server_launch_brave_legacy_key_fallback() -> None:
    """Старый ключ BRAVE_API_KEY должен подхватываться как BRAVE_SEARCH_API_KEY."""
    fake_env = {"BRAVE_API_KEY": "brave-legacy-key"}
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            launch = resolve_managed_server_launch("brave-search")
    assert launch["env"]["BRAVE_SEARCH_API_KEY"] == "brave-legacy-key"
    assert launch["missing_env"] == []


def test_resolve_managed_server_launch_github_legacy_token_fallback() -> None:
    """Старое имя GITHUB_PERSONAL_ACCESS_TOKEN должно работать как GITHUB_TOKEN."""
    fake_env = {"GITHUB_PERSONAL_ACCESS_TOKEN": "gh-old-token"}
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            launch = resolve_managed_server_launch("github")
    assert launch["env"]["GITHUB_TOKEN"] == "gh-old-token"
    assert launch["missing_env"] == []


# ──────────────────────────────────────────────────────────────────────────────
# build_lmstudio_mcp_json
# ──────────────────────────────────────────────────────────────────────────────


def test_build_lmstudio_mcp_json_skips_missing_optional_and_high_risk() -> None:
    """Без ключей и при exclude_high_risk: только no-key/low-risk серверы."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=False,
                include_high_risk=False,
            )
    servers = payload["mcpServers"]
    assert "shell" not in servers
    assert "filesystem-home" not in servers
    assert "context7" not in servers
    assert "shell" in summary["skipped_risk"]
    assert "context7" in summary["skipped_missing"]


def test_build_lmstudio_mcp_json_includes_all_with_keys_and_high_risk() -> None:
    """Если все ключи заданы и high_risk разрешён — все серверы попадают в mcp.json."""
    fake_env = {
        "CONTEXT7_API_KEY": "ctx-key",
        "BRAVE_SEARCH_API_KEY": "brave-key",
        "GITHUB_TOKEN": "gh-token",
        "FIRECRAWL_API_KEY": "fc-key",
        "OPENAI_API_KEY": "oai-key",
    }
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value=fake_env):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=True,
                include_high_risk=True,
            )
    assert "shell" in payload["mcpServers"]
    assert "filesystem-home" in payload["mcpServers"]
    assert summary["skipped_risk"] == []
    assert summary["skipped_missing"] == []


def test_build_lmstudio_mcp_json_summary_has_managed_names() -> None:
    """Summary должен содержать полный список managed_names."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            _payload, summary = build_lmstudio_mcp_json()
    assert "managed_names" in summary
    assert isinstance(summary["managed_names"], list)
    assert "filesystem" in summary["managed_names"]


def test_build_lmstudio_mcp_json_mcp_servers_use_wrapper_args() -> None:
    """Каждый included сервер должен использовать wrapper-скрипт с именем сервера в args."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=False,
                include_high_risk=False,
            )
    for name, entry in payload["mcpServers"].items():
        assert "args" in entry, f"Сервер {name!r} должен иметь args"
        assert entry["args"][-1] == name, (
            f"Последний arg должен быть именем сервера, получен {entry['args']!r}"
        )


def test_build_lmstudio_mcp_json_skips_github_without_token() -> None:
    """github не попадает в mcp.json если GITHUB_TOKEN отсутствует."""
    with patch.dict(os.environ, {}, clear=True):
        with patch("src.core.mcp_registry.load_project_env", return_value={}):
            payload, summary = build_lmstudio_mcp_json(
                include_optional_missing=False,
                include_high_risk=True,
            )
    assert "github" not in payload["mcpServers"]
    assert "github" in summary["skipped_missing"]
