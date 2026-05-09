#!/usr/bin/env python3
"""Wave 44-R-script-tools — отправка !swarm команды в Krab Swarm group.

Использование:
    python krab_send_to_swarm.py --text "!swarm task create --auto analysts ..."
    python krab_send_to_swarm.py --text "..." --topic 5

Возвращает JSON: {"ok": true, "message_id": 123, "chat_id": ..., "timestamp": ...}
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    KRAB_SWARM_GROUP_ID,
    SESSION_DIR,
    SESSION_NAME,
    emit_error,
    emit_json,
    get_telegram_credentials,
)

SCRIPT = "krab_send_to_swarm.py"


async def _send(text: str, topic_id: int | None) -> dict:
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
        kwargs: dict = {"chat_id": KRAB_SWARM_GROUP_ID, "text": text}
        if topic_id is not None:
            kwargs["message_thread_id"] = topic_id
        msg = await client.send_message(**kwargs)
        return {
            "ok": True,
            "message_id": msg.id,
            "chat_id": KRAB_SWARM_GROUP_ID,
            "topic_id": topic_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send !swarm command to Krab Swarm group")
    parser.add_argument("--text", required=True, help="Сообщение (например !swarm ...)")
    parser.add_argument("--topic", type=int, default=None, help="Topic id (None = General)")
    args = parser.parse_args(argv)

    if not args.text.strip():
        return emit_error("text is empty", SCRIPT, sys.argv[1:], hint="--text required")

    try:
        result = asyncio.run(_send(args.text, args.topic))
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
