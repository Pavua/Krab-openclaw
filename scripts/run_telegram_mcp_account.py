#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_telegram_mcp_account.py — wrapper для запуска Telegram MCP с отдельной session name.

Зачем нужен:
- позволяет поднять несколько Telegram MCP серверов рядом, не перелогинивая
  основной userbot/MCP-контур;
- даёт чистый способ завести отдельный test-account для Codex/Claude;
- избегает ручного копипаста env перед каждым запуском.

Как связан с системой:
- использует тот же `mcp-servers/telegram/server.py`, что и основной MCP;
- меняет только `TELEGRAM_SESSION_NAME`, чтобы session-файлы были раздельными;
- подходит для записи второго entry в `~/.codex/config.toml`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "mcp-servers" / "telegram" / "server.py"
ENV_PATH = ROOT / ".env"


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    """Разбирает только собственные аргументы и оставляет хвост для MCP server."""
    parser = argparse.ArgumentParser(
        description="Запускает Telegram MCP с отдельной session name.",
        add_help=True,
    )
    parser.add_argument(
        "--session-name",
        default="krab_test",
        help="Базовое имя Telegram session без суффикса _mcp (default: krab_test)",
    )
    parser.add_argument(
        "--session-dir",
        default="",
        help="Опциональная директория для MCP session-файлов. По умолчанию используется ~/.krab_mcp_sessions",
    )
    return parser.parse_known_args()


def main() -> int:
    """Подготавливает env и передаёт управление штатному Telegram MCP server."""
    args, passthrough = parse_args()
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    os.environ["TELEGRAM_SESSION_NAME"] = str(args.session_name or "krab_test").strip() or "krab_test"
    if str(args.session_dir or "").strip():
        os.environ["MCP_TELEGRAM_SESSION_DIR"] = str(args.session_dir).strip()

    cmd = [sys.executable, str(SERVER_PATH), *passthrough]
    os.execvpe(sys.executable, cmd, os.environ)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
