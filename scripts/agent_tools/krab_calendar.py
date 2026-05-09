#!/usr/bin/env python3
"""Wave 44-T-apple-apps — Apple Calendar via osascript.

Subcommands: events, create, delete.
ISO datetime input (YYYY-MM-DDTHH:MM[:SS]).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_calendar.py"


def _run_osa(script: str, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_iso(value: str) -> datetime:
    # Accept YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS]
    return datetime.fromisoformat(value)


def _as_applescript_date(dt: datetime) -> str:
    # AppleScript: "date \"M/D/YYYY H:MM:SS AM/PM\"" — use a portable form.
    return dt.strftime("%m/%d/%Y %I:%M:%S %p")


def cmd_events(args: argparse.Namespace) -> dict:
    start = _parse_iso(args.start)
    end = _parse_iso(args.end) if args.end else start + timedelta(days=1)
    s_str = _as_applescript_date(start)
    e_str = _as_applescript_date(end)
    script = (
        f'set startDate to date "{s_str}"\n'
        f'set endDate to date "{e_str}"\n'
        'tell application "Calendar"\n'
        "  set out to {}\n"
        "  repeat with cal in calendars\n"
        "    set evs to (every event of cal whose start date is greater than or equal to "
        "startDate and start date is less than endDate)\n"
        "    repeat with ev in evs\n"
        '      set end of out to (uid of ev) & "|||" & (summary of ev) & "|||" & '
        '((start date of ev) as string) & "|||" & ((end date of ev) as string)\n'
        "    end repeat\n"
        "  end repeat\n"
        '  set AppleScript\'s text item delimiters to "\\n"\n'
        "  return out as text\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script, timeout=120)
    if rc != 0:
        return {"ok": False, "error": stderr or "events fetch failed"}
    events = []
    for line in stdout.splitlines():
        parts = line.split("|||")
        if len(parts) >= 4:
            events.append(
                {
                    "id": parts[0].strip(),
                    "title": parts[1].strip(),
                    "start": parts[2].strip(),
                    "end": parts[3].strip(),
                }
            )
    return {"ok": True, "count": len(events), "events": events}


def cmd_create(args: argparse.Namespace) -> dict:
    start = _parse_iso(args.start)
    duration = int(args.duration or 60)
    end = start + timedelta(minutes=duration)
    title = _escape(args.title)
    s_str = _as_applescript_date(start)
    e_str = _as_applescript_date(end)
    location = _escape(args.location or "")
    notes = _escape(args.notes or "")
    cal = _escape(args.calendar or "Calendar")
    script = (
        f'set startDate to date "{s_str}"\n'
        f'set endDate to date "{e_str}"\n'
        'tell application "Calendar"\n'
        f'  set targetCal to first calendar whose name is "{cal}"\n'
        f"  set newEvent to make new event at end of events of targetCal with properties "
        f'{{summary:"{title}", start date:startDate, end date:endDate, '
        f'location:"{location}", description:"{notes}"}}\n'
        "  return uid of newEvent\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "create failed"}
    return {
        "ok": True,
        "id": stdout.strip(),
        "title": args.title,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }


def cmd_delete(args: argparse.Namespace) -> dict:
    uid = _escape(args.id)
    script = (
        'tell application "Calendar"\n'
        "  repeat with cal in calendars\n"
        f'    set evs to (every event of cal whose uid is "{uid}")\n'
        "    repeat with ev in evs\n"
        "      delete ev\n"
        "    end repeat\n"
        "  end repeat\n"
        '  return "deleted"\n'
        "end tell"
    )
    rc, _stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "delete failed"}
    return {"ok": True, "id": args.id}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apple Calendar bash interface")
    sub = parser.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("events")
    e.add_argument("--start", required=True)
    e.add_argument("--end", default=None)
    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--start", required=True)
    c.add_argument("--duration", type=int, default=60, help="minutes")
    c.add_argument("--location", default="")
    c.add_argument("--notes", default="")
    c.add_argument("--calendar", default="Calendar")
    d = sub.add_parser("delete")
    d.add_argument("--id", required=True)

    args = parser.parse_args(argv)
    handlers = {"events": cmd_events, "create": cmd_create, "delete": cmd_delete}
    try:
        result = handlers[args.cmd](args)
    except ValueError as exc:
        return emit_error(f"bad ISO datetime: {exc}", SCRIPT, sys.argv[1:])
    except subprocess.TimeoutExpired:
        return emit_error("osascript timeout", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
