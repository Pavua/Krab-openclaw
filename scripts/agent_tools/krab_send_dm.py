#!/usr/bin/env python3
"""Wave 44-R-script-tools — отправка сообщения в любой разрешённый chat_id.

По умолчанию разрешены: Krab Swarm group + owner DM. --allow-any
открывает любой chat (требует явного флага).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    DEFAULT_ALLOWED_CHAT_IDS,
    SESSION_DIR,
    SESSION_NAME,
    emit_error,
    emit_json,
    get_telegram_credentials,
)

SCRIPT = "krab_send_dm.py"


async def _send(chat_id: int, text: str, reply_to: int | None) -> dict:
    from pyrogram import Client  # type: ignore

    api_id, api_hash = get_telegram_credentials()
    client = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        no_updates=True,
    )
    async with client:
        kwargs: dict = {"chat_id": chat_id, "text": text}
        if reply_to is not None:
            kwargs["reply_to_message_id"] = reply_to
        msg = await client.send_message(**kwargs)
        return {
            "ok": True,
            "message_id": msg.id,
            "chat_id": chat_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send message to allowed chat_id")
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--text", required=True)
    parser.add_argument("--reply-to", type=int, default=None)
    parser.add_argument(
        "--allow-any", action="store_true", help="Override whitelist (use with care)"
    )
    args = parser.parse_args(argv)

    if not args.text.strip():
        return emit_error("text is empty", SCRIPT, sys.argv[1:])

    if args.chat_id not in DEFAULT_ALLOWED_CHAT_IDS and not args.allow_any:
        return emit_error(
            f"chat_id {args.chat_id} not whitelisted",
            SCRIPT,
            sys.argv[1:],
            hint="add --allow-any to override (allowed: "
            + ",".join(str(c) for c in sorted(DEFAULT_ALLOWED_CHAT_IDS))
            + ")",
        )

    try:
        result = asyncio.run(_send(args.chat_id, args.text, args.reply_to))
    except ImportError as exc:
        return emit_error(
            f"pyrogram not available: {exc}", SCRIPT, sys.argv[1:], hint="run via venv/bin/python"
        )
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
