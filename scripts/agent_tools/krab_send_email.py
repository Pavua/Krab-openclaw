#!/usr/bin/env python3
"""Wave 44-T-multi-channel — отправка Email через Mail.app (osascript).

Default = DRAFT. Только --send actually отправляет (extra safety).

First-time-to-recipient guard: persisted в
~/.openclaw/krab_runtime_state/email_known.json. Новый recipient →
требует --first-time-confirm флаг.
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
    EMAIL_KNOWN_PATH,
    audit_event,
    first_time_gate,
    is_hard_blocked,
    remember_recipient,
)

SCRIPT = "krab_send_email.py"


def _build_applescript(
    to_addr: str,
    subject: str,
    body: str,
    cc_addr: str | None,
    attachment: str | None,
    send: bool,
) -> str:
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    parts = [
        'tell application "Mail"',
        f'  set newMessage to make new outgoing message with properties {{subject:"{esc(subject)}", content:"{esc(body)}", visible:false}}',
        "  tell newMessage",
        f'    make new to recipient at end of to recipients with properties {{address:"{esc(to_addr)}"}}',
    ]
    if cc_addr:
        parts.append(
            f'    make new cc recipient at end of cc recipients with properties {{address:"{esc(cc_addr)}"}}'
        )
    if attachment:
        parts.append(
            f'    make new attachment with properties {{file name:(POSIX file "{esc(attachment)}")}} at after the last paragraph'
        )
    parts.append("  end tell")
    if send:
        parts.append("  send newMessage")
    else:
        parts.append("  save newMessage")
    parts.append("end tell")
    return "\n".join(parts)


def _run_applescript(script: str) -> dict:
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ},
        check=False,
    )
    return {
        "rc": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send email via Mail.app")
    parser.add_argument("--to", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--cc", default=None)
    parser.add_argument("--attachment", default=None)
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually send (default = save as draft)",
    )
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Force draft-only (overrides --send)",
    )
    parser.add_argument("--first-time-confirm", action="store_true")
    parser.add_argument("--owner-token", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not args.subject.strip() or not args.body.strip():
        return emit_error("subject or body empty", SCRIPT, sys.argv[1:])

    if args.attachment and not Path(args.attachment).is_file():
        return emit_error(f"attachment not found: {args.attachment}", SCRIPT, sys.argv[1:])

    to_addr = args.to.strip()

    blocked, reason = is_hard_blocked(to_addr)
    if blocked:
        audit_event("email", to_addr, "blocked", False, {"reason": reason})
        return emit_error(f"hard-blocked: {reason}", SCRIPT, sys.argv[1:])

    if args.cc:
        cc_blocked, cc_reason = is_hard_blocked(args.cc)
        if cc_blocked:
            audit_event("email", args.cc, "blocked_cc", False, {"reason": cc_reason})
            return emit_error(f"cc hard-blocked: {cc_reason}", SCRIPT, sys.argv[1:])

    allowed, gate_reason = first_time_gate(
        EMAIL_KNOWN_PATH,
        to_addr,
        args.first_time_confirm,
        args.owner_token,
    )
    if not allowed:
        audit_event("email", to_addr, "first_time_blocked", False, {"reason": gate_reason})
        return emit_error(
            "first_time_no_confirm",
            SCRIPT,
            sys.argv[1:],
            hint=(f"first time to {to_addr}; pass --first-time-confirm or --owner-token <token>"),
        )

    do_send = args.send and not args.no_send
    sent_or_draft = "sent" if do_send else "draft"

    if args.dry_run:
        result = {
            "ok": True,
            "recipients": [to_addr] + ([args.cc] if args.cc else []),
            "sent_or_draft": sent_or_draft,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "dry_run": True,
            "gate_reason": gate_reason,
        }
        emit_json(result, SCRIPT, sys.argv[1:])
        return 0

    script = _build_applescript(to_addr, args.subject, args.body, args.cc, args.attachment, do_send)
    try:
        res = _run_applescript(script)
    except (subprocess.TimeoutExpired, OSError) as exc:
        audit_event("email", to_addr, "exec_error", False, {"err": str(exc)})
        return emit_error(f"osascript failed: {exc}", SCRIPT, sys.argv[1:])

    if res["rc"] != 0:
        audit_event("email", to_addr, "osascript_error", False, {"stderr": res["stderr"]})
        return emit_error(
            f"osascript rc={res['rc']}: {res['stderr']}",
            SCRIPT,
            sys.argv[1:],
        )

    remember_recipient(EMAIL_KNOWN_PATH, to_addr, {})
    payload = {
        "ok": True,
        "message_id": res.get("stdout", ""),
        "recipients": [to_addr] + ([args.cc] if args.cc else []),
        "sent_or_draft": sent_or_draft,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gate_reason": gate_reason,
    }
    audit_event("email", to_addr, sent_or_draft, True, {})
    emit_json(payload, SCRIPT, sys.argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
