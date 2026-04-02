#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_telegram_mcp_configs.py — синхронизирует локальные Telegram MCP-конфиги Codex и Claude.

Зачем нужен:
- убирает drift между `~/.codex/config.toml` и
  `~/Library/Application Support/Claude/claude_desktop_config.json`;
- гарантирует, что оба AI-клиента поднимают одни и те же два Telegram MCP
  контура: аккаунт Краба (`kraab`) и аккаунт владельца (`p0lrd_cc`);
- переводит оба клиента на единый Python из project `venv`, чтобы не ловить
  расхождения зависимостей между `venv` и `.venv`.

Как связан с системой:
- использует существующий wrapper `scripts/run_telegram_mcp_account.py`;
- перед записью делает timestamp backup обоих конфигов рядом с оригиналом;
- не трогает остальные настройки Codex и Claude вне MCP-блоков Telegram;
- вычищает устаревшие Telegram MCP entry в Claude, чтобы параллельно не жили
  старые `.venv`/legacy server-конфиги и не путали диагностику.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PYTHON = ROOT / "venv" / "bin" / "python"
WRAPPER = ROOT / "scripts" / "run_telegram_mcp_account.py"

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CLAUDE_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


@dataclass(frozen=True)
class CodexMcpSpec:
    """Описание одного MCP entry для Codex config.toml."""

    section_name: str
    session_name: str


CODEX_SPECS = (
    CodexMcpSpec(section_name="krab-telegram", session_name="kraab"),
    CodexMcpSpec(section_name="krab-telegram-test", session_name="p0lrd_cc"),
)

CLAUDE_CANONICAL_NAMES = ("krab-yung-nagato", "krab-p0lrd")
CLAUDE_LEGACY_NAMES = ("krab-telegram", "krab-telegram-test")


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%SZ")


def backup_file(path: Path) -> Path | None:
    """Создаёт timestamp backup исходного файла, если он существует."""
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak_{_timestamp()}")
    backup.write_text(path.read_text())
    return backup


def _codex_section_text(spec: CodexMcpSpec) -> str:
    """Строит канонический TOML-блок для одного Telegram MCP сервера Codex."""
    return (
        f"[mcp_servers.{spec.section_name}]\n"
        f"command = '{PROJECT_PYTHON}'\n"
        f"args = ['{WRAPPER}', '--session-name', '{spec.session_name}', '--transport', 'stdio']\n"
        "startup_timeout_ms = 20000\n"
    )


def sync_codex_config_text(text: str) -> str:
    """Синхронизирует только Telegram MCP-секции в config.toml."""
    result = text
    for spec in CODEX_SPECS:
        pattern = re.compile(
            rf"(?ms)^\[mcp_servers\.{re.escape(spec.section_name)}\]\n.*?(?=^\[|\Z)"
        )
        replacement = _codex_section_text(spec).rstrip() + "\n\n"
        if pattern.search(result):
            result = pattern.sub(replacement, result, count=1)
        else:
            if not result.endswith("\n"):
                result += "\n"
            result += "\n" + replacement
    return result


def sync_claude_config_text(text: str) -> str:
    """Синхронизирует Telegram MCP entries в Claude Desktop JSON-конфиге."""
    payload = json.loads(text)
    mcp_servers = payload.setdefault("mcpServers", {})

    command = str(PROJECT_PYTHON)
    wrapper = str(WRAPPER)

    # Сначала вычищаем legacy entry, чтобы Claude не пытался поднимать
    # несколько Telegram MCP-контуров параллельно из старых конфигов.
    for legacy_name in CLAUDE_LEGACY_NAMES:
        mcp_servers.pop(legacy_name, None)

    mcp_servers["krab-yung-nagato"] = {
        "command": command,
        "args": [wrapper, "--session-name", "kraab", "--transport", "stdio"],
    }
    mcp_servers["krab-p0lrd"] = {
        "command": command,
        "args": [wrapper, "--session-name", "p0lrd_cc", "--transport", "stdio"],
    }

    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def sync_codex_config(path: Path = CODEX_CONFIG) -> dict[str, str | bool]:
    """Применяет sync к Codex config.toml."""
    if not path.exists():
        return {"updated": False, "reason": "missing", "path": str(path)}
    original = path.read_text()
    updated = sync_codex_config_text(original)
    changed = updated != original
    if changed:
        backup_file(path)
        path.write_text(updated)
    return {"updated": changed, "reason": "ok", "path": str(path)}


def sync_claude_config(path: Path = CLAUDE_CONFIG) -> dict[str, str | bool]:
    """Применяет sync к Claude Desktop JSON-конфигу."""
    if not path.exists():
        return {"updated": False, "reason": "missing", "path": str(path)}
    original = path.read_text()
    updated = sync_claude_config_text(original)
    changed = updated != original
    if changed:
        backup_file(path)
        path.write_text(updated)
    return {"updated": changed, "reason": "ok", "path": str(path)}


def main() -> int:
    """Точка входа CLI: синхронизирует оба клиента и печатает JSON-отчёт."""
    report = {
        "codex": sync_codex_config(),
        "claude": sync_claude_config(),
        "python": str(PROJECT_PYTHON),
        "wrapper": str(WRAPPER),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
