#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_telegram_mcp_accounts.py — прямой smoke-check Telegram MCP аккаунтов.

Зачем нужен:
- отделяет реальную работоспособность Telegram bridge от состояния MCP-хоста
  внутри Codex/Claude;
- быстро доказывает, что оба session-контура (`kraab` и `p0lrd_cc`) живы,
  умеют читать диалоги, историю и глобальный поиск;
- опционально делает контрольную отправку сообщения, если нужна живая
  end-to-end проверка доставки.

Как связан с системой:
- использует тот же `mcp-servers/telegram/telegram_bridge.py`, что и MCP сервер;
- поднимает bridge напрямую через project `venv`, без участия Codex/Claude;
- полезен после `Sync Telegram MCP Configs.command`, чтобы понять, проблема в
  Telegram/Pyrogram или только в том, что хост ещё не перечитал конфиг.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
BRIDGE_DIR = ROOT / "mcp-servers" / "telegram"

if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from telegram_bridge import TelegramBridge  # noqa: E402


ACCOUNTS: tuple[tuple[str, str], ...] = (
    ("krab", "kraab"),
    ("owner", "p0lrd_cc"),
)


def parse_args() -> argparse.Namespace:
    """Разбирает CLI-параметры smoke-проверки."""
    parser = argparse.ArgumentParser(
        description="Прямая проверка Telegram MCP аккаунтов без участия MCP-хоста.",
    )
    parser.add_argument(
        "--history-chat",
        default="p0lrd",
        help="Чат для проверки истории сообщений (default: p0lrd)",
    )
    parser.add_argument(
        "--search-query",
        default="Codex",
        help="Строка для глобального поиска (default: Codex)",
    )
    parser.add_argument(
        "--dialogs-limit",
        type=int,
        default=5,
        help="Сколько диалогов читать при smoke-check (default: 5)",
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=3,
        help="Сколько сообщений читать из истории (default: 3)",
    )
    parser.add_argument(
        "--search-limit",
        type=int,
        default=3,
        help="Сколько результатов получать из глобального поиска (default: 3)",
    )
    parser.add_argument(
        "--send-test-chat",
        default="",
        help="Опционально: чат для контрольной отправки сообщения",
    )
    return parser.parse_args()


async def verify_account(
    *,
    label: str,
    session_name: str,
    history_chat: str,
    search_query: str,
    dialogs_limit: int,
    history_limit: int,
    search_limit: int,
    send_test_chat: str,
) -> dict[str, Any]:
    """Проверяет один Telegram session-контур и возвращает JSON-отчёт."""
    os.environ["TELEGRAM_SESSION_NAME"] = session_name
    bridge = TelegramBridge()
    report: dict[str, Any] = {
        "label": label,
        "session_name": session_name,
        "ok": False,
    }
    await bridge.start()
    try:
        dialogs = await bridge.get_dialogs(limit=dialogs_limit)
        history = await bridge.get_chat_history(history_chat, limit=history_limit)
        search = await bridge.search(search_query, limit=search_limit)

        report.update(
            {
                "ok": True,
                "dialogs_count": len(dialogs),
                "history_count": len(history),
                "search_count": len(search),
                "sample_dialog": dialogs[0] if dialogs else None,
                "sample_history": history[0] if history else None,
                "sample_search": search[0] if search else None,
            }
        )

        if send_test_chat:
            text = f"MCP_VERIFY_{label}_{session_name}_2026-04-03"
            sent = await bridge.send_message(send_test_chat, text)
            report["send_test"] = {
                "chat": send_test_chat,
                "message_id": sent.get("id"),
                "text": sent.get("text"),
                "chat_id": sent.get("chat_id"),
            }
    except Exception as exc:  # noqa: BLE001
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        await bridge.stop()
    return report


async def amain(args: argparse.Namespace) -> int:
    """Запускает smoke-проверку по всем каноническим аккаунтам."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)

    reports = []
    for label, session_name in ACCOUNTS:
        reports.append(
            await verify_account(
                label=label,
                session_name=session_name,
                history_chat=args.history_chat,
                search_query=args.search_query,
                dialogs_limit=args.dialogs_limit,
                history_limit=args.history_limit,
                search_limit=args.search_limit,
                send_test_chat=args.send_test_chat.strip(),
            )
        )

    payload = {
        "ok": all(item.get("ok") for item in reports),
        "history_chat": args.history_chat,
        "search_query": args.search_query,
        "reports": reports,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def main() -> int:
    """CLI entrypoint."""
    return asyncio.run(amain(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
