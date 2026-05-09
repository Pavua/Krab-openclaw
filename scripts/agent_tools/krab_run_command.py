#!/usr/bin/env python3
"""Wave 44-R-script-tools — выполнение Krab !command через owner DM.

Полноценное выполнение handle_* requires live Pyrogram client, поэтому
скрипт делает следующее:

1. Для безопасных read-only команд (!status, !help, !swarm summary) —
   пробует HTTP /api/* endpoint (owner panel :8080). Это даёт честный
   результат без duplicate-instance проблем.
2. Для всех остальных !cmd — отправляет в owner DM (chat_id=312322764)
   через kraab.session, и работающий Krab userbot обработает их сам.

Возвращает JSON с результатом или информацией о доставке команды.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    OWNER_DM_ID,
    SESSION_DIR,
    SESSION_NAME,
    emit_error,
    emit_json,
    get_telegram_credentials,
)

SCRIPT = "krab_run_command.py"
OWNER_PANEL_BASE = "http://127.0.0.1:8080"

# Map of !command → owner-panel HTTP endpoint (read-only).
HTTP_BACKED: dict[str, str] = {
    "!status": "/api/health",
    "!stats": "/api/stats",
    "!swarm summary": "/api/swarm/summary",
}


def _try_http(command: str) -> dict | None:
    endpoint = HTTP_BACKED.get(command.strip().lower())
    if not endpoint:
        return None
    url = OWNER_PANEL_BASE + endpoint
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            payload_raw = resp.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(payload_raw)
        except json.JSONDecodeError:
            data = {"raw": payload_raw[:2000]}
        return {
            "ok": True,
            "mode": "http",
            "command": command,
            "endpoint": endpoint,
            "result": data,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "mode": "http",
            "command": command,
            "endpoint": endpoint,
            "error": f"http unreachable: {exc}",
        }


async def _send_to_owner(command: str) -> dict:
    """Fallback: doc owner DM, where running userbot handles the !cmd."""
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
        msg = await client.send_message(chat_id=OWNER_DM_ID, text=command)
        return {
            "ok": True,
            "mode": "dm-delivery",
            "command": command,
            "message_id": msg.id,
            "chat_id": OWNER_DM_ID,
            "note": "Команда доставлена в owner DM; Krab userbot обработает её. "
            "Результат — в Telegram, не в этом JSON.",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Krab !command")
    parser.add_argument("--command", required=True)
    parser.add_argument(
        "--prefer-dm", action="store_true", help="Skip HTTP attempt, force DM delivery"
    )
    args = parser.parse_args(argv)

    cmd = args.command.strip()
    if not cmd.startswith("!"):
        return emit_error("command must start with !", SCRIPT, sys.argv[1:])

    if not args.prefer_dm:
        http_result = _try_http(cmd)
        if http_result and http_result.get("ok"):
            emit_json(http_result, SCRIPT, sys.argv[1:])
            return 0

    try:
        result = asyncio.run(_send_to_owner(cmd))
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
