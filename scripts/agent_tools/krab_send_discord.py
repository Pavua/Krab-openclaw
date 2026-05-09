#!/usr/bin/env python3
"""Wave 44-T-multi-channel — отправка сообщения в Discord.

Стратегия:
1. Webhook URL из env (KRAB_DISCORD_WEBHOOK_<SERVER>_<CHANNEL> или KRAB_DISCORD_WEBHOOK_URL).
2. Если webhook не configured — return ok=false с понятным hint.

First-time-to-channel: requires --first-time-confirm флаг.
Persisted в ~/.openclaw/krab_runtime_state/discord_known_channels.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402
from _multi_channel_helpers import (  # noqa: E402
    DISCORD_KNOWN_PATH,
    audit_event,
    discord_webhook_for,
    first_time_gate,
    is_hard_blocked,
    remember_recipient,
)

SCRIPT = "krab_send_discord.py"


def _post_webhook(url: str, content: str) -> dict:
    """POST {"content": ...} в webhook URL. Returns parsed result."""
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "krab-agent/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        msg_id = ""
        try:
            parsed = json.loads(body) if body else {}
            msg_id = str(parsed.get("id", ""))
        except json.JSONDecodeError:
            pass
        return {"status": resp.status, "message_id": msg_id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send message to Discord")
    parser.add_argument("--server", required=True, help="Discord server name or id")
    parser.add_argument("--channel", required=True, help="Discord channel name or id")
    parser.add_argument("--text", required=True)
    parser.add_argument("--first-time-confirm", action="store_true")
    parser.add_argument("--owner-token", default=None)
    args = parser.parse_args(argv)

    if not args.text.strip():
        return emit_error("text is empty", SCRIPT, sys.argv[1:])

    recipient_key = f"{args.server}#{args.channel}"

    blocked, reason = is_hard_blocked(recipient_key)
    if blocked:
        audit_event("discord", recipient_key, "blocked", False, {"reason": reason})
        return emit_error(f"hard-blocked: {reason}", SCRIPT, sys.argv[1:])

    allowed, gate_reason = first_time_gate(
        DISCORD_KNOWN_PATH,
        recipient_key,
        args.first_time_confirm,
        args.owner_token,
    )
    if not allowed:
        audit_event("discord", recipient_key, "first_time_blocked", False, {"reason": gate_reason})
        return emit_error(
            "first_time_no_confirm",
            SCRIPT,
            sys.argv[1:],
            hint=(
                f"first time to {recipient_key}; pass --first-time-confirm or --owner-token <token>"
            ),
        )

    webhook = discord_webhook_for(args.server, args.channel)
    if not webhook:
        result = {
            "ok": False,
            "error": "discord not configured",
            "hint": (
                "set KRAB_DISCORD_WEBHOOK_URL env var (or per-channel "
                "KRAB_DISCORD_WEBHOOK_<SERVER>_<CHANNEL>) to a webhook URL"
            ),
        }
        audit_event("discord", recipient_key, "not_configured", False, {})
        emit_json(result, SCRIPT, sys.argv[1:])
        return 1

    try:
        post_res = _post_webhook(webhook, args.text)
    except urllib.error.HTTPError as exc:
        audit_event("discord", recipient_key, "http_error", False, {"code": exc.code})
        return emit_error(f"HTTP {exc.code}: {exc.reason}", SCRIPT, sys.argv[1:])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        audit_event("discord", recipient_key, "network_error", False, {"err": str(exc)})
        return emit_error(f"network error: {exc}", SCRIPT, sys.argv[1:])

    remember_recipient(
        DISCORD_KNOWN_PATH,
        recipient_key,
        {"server": args.server, "channel": args.channel},
    )
    payload = {
        "ok": True,
        "message_id": post_res.get("message_id", ""),
        "server": args.server,
        "channel": args.channel,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gate_reason": gate_reason,
    }
    audit_event("discord", recipient_key, "sent", True, {"message_id": payload["message_id"]})
    emit_json(payload, SCRIPT, sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
