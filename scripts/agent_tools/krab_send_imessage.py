#!/usr/bin/env python3
"""Wave 44-T-multi-channel — отправка iMessage через osascript.

Стратегия: AppleScript `tell application "Messages" to send "..." to buddy "..."`.

First-time guard: persisted в ~/.openclaw/krab_runtime_state/imessage_known.json.
Новый recipient → требует --first-time-confirm флаг.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402
from _multi_channel_helpers import (  # noqa: E402
    IMESSAGE_KNOWN_PATH,
    audit_event,
    first_time_gate,
    is_hard_blocked,
    remember_recipient,
)

SCRIPT = "krab_send_imessage.py"


def _send_via_osascript(recipient: str, text: str, service: str = "iMessage") -> dict:
    """Использует osascript для send via Messages.app.

    Args:
        recipient: phone number (+1...) или Apple ID email.
        text: message body.
        service: "iMessage" или "SMS".
    """
    # Экранируем кавычки
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    safe_recipient = recipient.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "Messages"\n'
        f"  set targetService to 1st service whose service type = {service}\n"
        f'  set targetBuddy to buddy "{safe_recipient}" of targetService\n'
        f'  send "{safe_text}" to targetBuddy\n'
        f"end tell"
    )
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=20,
        env={**os.environ},
        check=False,
    )
    return {
        "rc": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send iMessage via Messages.app")
    parser.add_argument("--to", required=True, help="phone (+1...) or Apple ID email")
    parser.add_argument("--text", required=True)
    parser.add_argument("--service", default="iMessage", choices=["iMessage", "SMS"])
    parser.add_argument("--first-time-confirm", action="store_true")
    parser.add_argument("--owner-token", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Skip actual osascript call")
    args = parser.parse_args(argv)

    if not args.text.strip():
        return emit_error("text is empty", SCRIPT, sys.argv[1:])

    recipient = args.to.strip()

    blocked, reason = is_hard_blocked(recipient)
    if blocked:
        audit_event("imessage", recipient, "blocked", False, {"reason": reason})
        return emit_error(f"hard-blocked: {reason}", SCRIPT, sys.argv[1:])

    allowed, gate_reason = first_time_gate(
        IMESSAGE_KNOWN_PATH,
        recipient,
        args.first_time_confirm,
        args.owner_token,
    )
    if not allowed:
        audit_event("imessage", recipient, "first_time_blocked", False, {"reason": gate_reason})
        return emit_error(
            "first_time_no_confirm",
            SCRIPT,
            sys.argv[1:],
            hint=(f"first time to {recipient}; pass --first-time-confirm or --owner-token <token>"),
        )

    if args.dry_run:
        remember_recipient(IMESSAGE_KNOWN_PATH, recipient, {"service": args.service})
        result = {
            "ok": True,
            "recipient": recipient,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "dry_run": True,
            "gate_reason": gate_reason,
        }
        emit_json(result, SCRIPT, sys.argv[1:])
        return 0

    try:
        res = _send_via_osascript(recipient, args.text, args.service)
    except (subprocess.TimeoutExpired, OSError) as exc:
        audit_event("imessage", recipient, "exec_error", False, {"err": str(exc)})
        return emit_error(f"osascript failed: {exc}", SCRIPT, sys.argv[1:])

    if res["rc"] != 0:
        audit_event("imessage", recipient, "osascript_error", False, {"stderr": res["stderr"]})
        return emit_error(
            f"osascript rc={res['rc']}: {res['stderr']}",
            SCRIPT,
            sys.argv[1:],
        )

    remember_recipient(IMESSAGE_KNOWN_PATH, recipient, {"service": args.service})
    payload = {
        "ok": True,
        "recipient": recipient,
        "service": args.service,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gate_reason": gate_reason,
    }
    audit_event("imessage", recipient, "sent", True, {})
    emit_json(payload, SCRIPT, sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
