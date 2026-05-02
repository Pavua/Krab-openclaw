#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Launcher для managed MCP-серверов проекта.

Связи:
- используется LM Studio через `~/.lmstudio/mcp.json`;
- использует единый реестр `src.core.mcp_registry`;
- нужен, чтобы GUI-приложения видели секреты из `.env` и запускали те же
  curated MCP-сервера, что и сам проект.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.mcp_registry import get_managed_mcp_servers, resolve_managed_server_launch


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы launcher-скрипта."""
    parser = argparse.ArgumentParser(description="Запуск managed MCP server по имени.")
    parser.add_argument("server_name", help="Идентификатор сервера из managed MCP registry.")
    parser.add_argument(
        "--print-command",
        action="store_true",
        help="Не запускать сервер, а только показать итоговую команду и env-статус.",
    )
    return parser.parse_args()


def main() -> int:
    """Точка входа launcher-скрипта."""
    args = parse_args()
    known = sorted(get_managed_mcp_servers())
    try:
        launch = resolve_managed_server_launch(args.server_name)
    except KeyError:
        print(
            f"❌ Неизвестный MCP сервер: {args.server_name}\n"
            f"Доступно: {', '.join(known)}",
            file=sys.stderr,
        )
        return 2

    missing_env = list(launch.get("missing_env", []))
    if args.print_command:
        print(f"name={args.server_name}")
        print(f"command={launch['command']}")
        print(f"args={launch['args']}")
        print(f"missing_env={missing_env}")
        if launch.get("manual_setup"):
            print(f"manual_setup={launch['manual_setup']}")
        return 0

    if missing_env:
        print(
            "❌ MCP сервер не может быть запущен: отсутствуют обязательные переменные "
            f"окружения: {', '.join(missing_env)}",
            file=sys.stderr,
        )
        return 3

    command = str(launch["command"])
    argv = [command, *[str(item) for item in launch.get("args", [])]]

    # Используем exec, чтобы процесс LM Studio видел именно реальный MCP-сервер,
    # а не промежуточный Python wrapper.
    os.execvpe(command, argv, launch["env"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
