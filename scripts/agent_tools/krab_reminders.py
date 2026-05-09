#!/usr/bin/env python3
"""Wave 44-T-apple-apps — Apple Reminders via osascript.

Subcommands: list, create, complete.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_reminders.py"
DEFAULT_LIST_NAME = "Reminders"


def _run_osa(script: str, timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _as_applescript_date(dt: datetime) -> str:
    return dt.strftime("%m/%d/%Y %I:%M:%S %p")


def cmd_list(args: argparse.Namespace) -> dict:
    list_name = _escape(args.list_name or DEFAULT_LIST_NAME)
    script = (
        'tell application "Reminders"\n'
        f'  set targetList to first list whose name is "{list_name}"\n'
        "  set out to {}\n"
        "  repeat with r in (reminders of targetList whose completed is false)\n"
        '    set end of out to (id of r) & "|||" & (name of r)\n'
        "  end repeat\n"
        '  set AppleScript\'s text item delimiters to "\\n"\n'
        "  return out as text\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script, timeout=60)
    if rc != 0:
        return {"ok": False, "error": stderr or "list failed"}
    items = []
    for line in stdout.splitlines():
        if "|||" in line:
            rid, _, name = line.partition("|||")
            items.append({"id": rid.strip(), "title": name.strip()})
    return {
        "ok": True,
        "list": args.list_name or DEFAULT_LIST_NAME,
        "count": len(items),
        "reminders": items,
    }


def cmd_create(args: argparse.Namespace) -> dict:
    title = _escape(args.title)
    list_name = _escape(args.list_name or DEFAULT_LIST_NAME)
    notes = _escape(args.notes or "")
    props = [f'name:"{title}"']
    if notes:
        props.append(f'body:"{notes}"')
    if args.due:
        try:
            due_dt = datetime.fromisoformat(args.due)
        except ValueError as exc:
            return {"ok": False, "error": f"bad ISO datetime: {exc}"}
        props.append(f'due date:date "{_as_applescript_date(due_dt)}"')
    props_str = ", ".join(props)
    script = (
        'tell application "Reminders"\n'
        f'  set targetList to first list whose name is "{list_name}"\n'
        f"  set newR to make new reminder at end of reminders of targetList "
        f"with properties {{{props_str}}}\n"
        "  return id of newR\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "create failed"}
    return {
        "ok": True,
        "id": stdout.strip(),
        "title": args.title,
        "list": args.list_name or DEFAULT_LIST_NAME,
    }


def cmd_complete(args: argparse.Namespace) -> dict:
    rid = _escape(args.id)
    script = (
        'tell application "Reminders"\n'
        f'  set r to first reminder whose id is "{rid}"\n'
        "  set completed of r to true\n"
        "  return id of r\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "complete failed"}
    return {"ok": True, "id": stdout.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apple Reminders bash interface")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lst = sub.add_parser("list")
    lst.add_argument("--list-name", default=None)
    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--due", default=None)
    c.add_argument("--list-name", default=None)
    c.add_argument("--notes", default="")
    cp = sub.add_parser("complete")
    cp.add_argument("--id", required=True)

    args = parser.parse_args(argv)
    handlers = {"list": cmd_list, "create": cmd_create, "complete": cmd_complete}
    try:
        result = handlers[args.cmd](args)
    except subprocess.TimeoutExpired:
        return emit_error("osascript timeout", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
