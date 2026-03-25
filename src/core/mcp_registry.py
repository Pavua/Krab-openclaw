# -*- coding: utf-8 -*-
"""
Единый реестр MCP-серверов для Krab/OpenClaw и LM Studio.

Зачем это нужно:
- LM Studio, Krab runtime и вспомогательные launcher-скрипты не должны жить
  на разных несогласованных наборах MCP-конфигов;
- секреты часто лежат в `.env` проекта, а GUI-приложения вроде LM Studio не
  видят их автоматически из shell-сессии;
- curated-список must-have серверов позволяет дать агентам файлы, браузер,
  документацию и память без постоянного ручного тюнинга.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from .lm_studio_auth import resolve_lm_studio_api_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_PATH = PROJECT_ROOT / ".env"
LMSTUDIO_MCP_PATH = Path.home() / ".lmstudio" / "mcp.json"


def load_project_env(env_path: Path | None = None) -> dict[str, str]:
    """
    Загружает `.env` проекта в обычный словарь без сторонних зависимостей.

    Важно:
    - shell env имеет приоритет над `.env`;
    - значения разворачивают `~` и `$VAR`, чтобы MCP-сервера получали уже
      нормализованные абсолютные пути.
    """
    path = env_path or PROJECT_ENV_PATH
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        expanded = os.path.expandvars(os.path.expanduser(value))
        loaded[key] = expanded
    return loaded


def _python_command() -> str:
    """Возвращает стабильный Python для launcher-скриптов LM Studio."""
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable or "/usr/bin/python3"


def _lmstudio_api_base_url(env: dict[str, str] | None = None) -> str:
    """Нормализует базовый URL LM Studio к OpenAI-compatible `/v1`."""
    scope = env or os.environ
    raw = str(scope.get("LM_STUDIO_URL", "http://127.0.0.1:1234") or "").strip().rstrip("/")
    if raw.endswith("/v1"):
        return raw
    return f"{raw}/v1"


def _lmstudio_mcp_api_key(env: dict[str, str] | None = None) -> str:
    """Возвращает ключ для OpenAI-compatible MCP bridge к LM Studio."""
    return resolve_lm_studio_api_key(env or os.environ) or "lm-studio"


def _brave_search_api_key(env: dict[str, str] | None = None) -> str:
    """Возвращает Brave Search API key с legacy fallback."""
    scope = env or os.environ
    return str(
        scope.get("BRAVE_SEARCH_API_KEY", "")
        or scope.get("BRAVE_API_KEY", "")
        or ""
    ).strip()


def _github_token(env: dict[str, str] | None = None) -> str:
    """Возвращает GitHub token с поддержкой старого имени переменной."""
    scope = env or os.environ
    return str(
        scope.get("GITHUB_TOKEN", "")
        or scope.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        or ""
    ).strip()


def _server(
    *,
    description: str,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
    required_env: list[str] | None = None,
    risk: str = "medium",
    manual_setup: list[str] | None = None,
) -> dict[str, Any]:
    """Удобный конструктор записи managed MCP server."""
    return {
        "description": description,
        "command": command,
        "args": list(args),
        "env": dict(env or {}),
        "required_env": list(required_env or []),
        "risk": risk,
        "manual_setup": list(manual_setup or []),
    }


def get_managed_mcp_servers() -> dict[str, dict[str, Any]]:
    """
    Возвращает curated-набор MCP-серверов.

    Принципы набора:
    - must-have для агента: файлы, браузер, память, shell;
    - docs/search интеграции включаем, но делаем честно optional по ключам;
    - отдельный browser server для OpenClaw relay и для обычного Chrome-профиля.
    """
    loaded_env = load_project_env()
    project_root = str(PROJECT_ROOT)
    home_root = str(Path.home())
    openclaw_browser_url = str(
        os.getenv(
            "MCP_OPENCLAW_BROWSER_URL",
            loaded_env.get("MCP_OPENCLAW_BROWSER_URL", "http://127.0.0.1:18800"),
        )
        or "http://127.0.0.1:18800"
    ).strip()

    return {
        "filesystem": _server(
            description="Доступ к файлам проекта Krab/OpenClaw.",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", project_root],
            risk="medium",
        ),
        "filesystem-home": _server(
            description="Широкий доступ к домашней директории пользователя.",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", home_root],
            risk="high",
        ),
        "shell": _server(
            description="Полноценный shell MCP. Сильный, но рискованный инструмент.",
            command="npx",
            args=["-y", "mcp-shell"],
            risk="high",
        ),
        "telegram": _server(
            description="Telegram MCP: чтение чатов, отправка, транскрипция голосовых, dev-утилиты Краба.",
            command=_python_command(),
            args=[str(PROJECT_ROOT / "mcp-servers" / "telegram" / "server.py"),
                  "--transport", "stdio"],
            risk="high",
        ),
        "memory": _server(
            description="Лёгкая MCP-память для заметок и промежуточных фактов.",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-memory"],
            risk="low",
        ),
        "github": _server(
            description="GitHub MCP для issue/PR/repo automation.",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={
                "GITHUB_API_URL": "https://api.github.com",
                "GITHUB_TOKEN": _github_token({**loaded_env, **os.environ}),
            },
            required_env=["GITHUB_TOKEN"],
            risk="medium",
        ),
        "firecrawl": _server(
            description="Глубокий web crawl/search/extract сервер.",
            command="npx",
            args=["-y", "firecrawl-mcp", "--max-concurrency", "5", "--timeout", "10000"],
            required_env=["FIRECRAWL_API_KEY"],
            risk="medium",
        ),
        "brave-search": _server(
            description="Быстрый web search через Brave Search MCP.",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-brave-search"],
            env={
                "BRAVE_SEARCH_API_KEY": _brave_search_api_key({**loaded_env, **os.environ}),
            },
            required_env=["BRAVE_SEARCH_API_KEY"],
            risk="low",
        ),
        "context7": _server(
            description="Актуальная документация библиотек и фреймворков.",
            command="npx",
            args=["-y", "@upstash/context7-mcp"],
            required_env=["CONTEXT7_API_KEY"],
            risk="low",
        ),
        "openai-chat": _server(
            description="OpenAI MCP bridge для внешних моделей/API.",
            command="npx",
            args=["-y", "@mzxrai/mcp-openai@latest"],
            required_env=["OPENAI_API_KEY"],
            risk="medium",
        ),
        "lmstudio": _server(
            description="OpenAI-compatible bridge к локальному LM Studio API.",
            command="npx",
            args=["-y", "@mzxrai/mcp-openai@latest"],
            env={
                "OPENAI_API_KEY": _lmstudio_mcp_api_key({**loaded_env, **os.environ}),
                "OPENAI_BASE_URL": _lmstudio_api_base_url({**loaded_env, **os.environ}),
            },
            risk="low",
        ),
        "chrome-profile": _server(
            description="Chrome DevTools MCP для обычного Chrome-профиля пользователя.",
            command="npx",
            args=[
                "-y",
                "chrome-devtools-mcp@latest",
                "--autoConnect",
                "--channel",
                "stable",
                "--no-usage-statistics",
            ],
            manual_setup=[
                "Запусти helper `new Open Owner Chrome Remote Debugging.command`, чтобы проверить ordinary Chrome path. На Chrome 146+ default profile может быть заблокирован политикой remote debugging и тогда нужен non-default data dir или Debug browser.",
                "Если LM Studio/Codex уже были запущены до relaunch Chrome, после attach перезапусти их, чтобы MCP перечитал состояние.",
            ],
            risk="medium",
        ),
        "openclaw-browser": _server(
            description="Chrome DevTools MCP поверх OpenClaw browser relay.",
            command="npx",
            args=[
                "-y",
                "chrome-devtools-mcp@latest",
                "--browserUrl",
                openclaw_browser_url,
                "--no-usage-statistics",
            ],
            risk="medium",
        ),
    }


def resolve_managed_server_launch(name: str) -> dict[str, Any]:
    """
    Возвращает готовую launch-конфигурацию сервера с уже собранным env.
    """
    servers = get_managed_mcp_servers()
    if name not in servers:
        raise KeyError(name)

    server = dict(servers[name])
    merged_env = dict(load_project_env())
    merged_env.update(os.environ)
    merged_env.update(server.get("env", {}))
    missing_env = [
        key for key in server.get("required_env", [])
        if not str(merged_env.get(key, "") or "").strip()
    ]

    return {
        "name": name,
        "description": server["description"],
        "command": str(server["command"]),
        "args": list(server.get("args", [])),
        "env": {key: str(value) for key, value in merged_env.items()},
        "missing_env": missing_env,
        "risk": server.get("risk", "medium"),
        "manual_setup": list(server.get("manual_setup", [])),
    }


def build_lmstudio_mcp_json(
    *,
    include_optional_missing: bool = False,
    include_high_risk: bool = True,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    """
    Строит `mcp.json` для LM Studio поверх managed launcher-скрипта.

    Возвращает:
    - готовый JSON-объект;
    - служебный summary со списками `included` и `skipped_missing`.
    """
    wrapper = PROJECT_ROOT / "scripts" / "run_managed_mcp_server.py"
    command = _python_command()
    included: list[str] = []
    skipped_missing: list[str] = []
    skipped_risk: list[str] = []
    mcp_servers: dict[str, Any] = {}

    for name in sorted(get_managed_mcp_servers()):
        launch = resolve_managed_server_launch(name)
        if launch["missing_env"] and not include_optional_missing:
            skipped_missing.append(name)
            continue
        if not include_high_risk and launch["risk"] == "high":
            skipped_risk.append(name)
            continue
        mcp_servers[name] = {
            "command": command,
            "args": [str(wrapper), name],
        }
        included.append(name)

    return {
        "mcpServers": mcp_servers,
    }, {
        "included": included,
        "skipped_missing": skipped_missing,
        "skipped_risk": skipped_risk,
        "managed_names": sorted(get_managed_mcp_servers()),
    }
