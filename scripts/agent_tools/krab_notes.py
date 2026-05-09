#!/usr/bin/env python3
"""Wave 44-T-apple-apps — Apple Notes via osascript.

Subcommands: list, get, create, search, update.
JSON output. Read-only friendly: list/get/search are non-destructive.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_notes.py"


def _run_osa(script: str, timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _escape(value: str) -> str:
    """Escape a string for embedding into AppleScript double-quoted literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cmd_list(_args: argparse.Namespace) -> dict:
    script = (
        'tell application "Notes"\n'
        "  set out to {}\n"
        "  repeat with n in notes\n"
        '    set end of out to (id of n) & "|||" & (name of n)\n'
        "  end repeat\n"
        '  set AppleScript\'s text item delimiters to "\\n"\n'
        "  return out as text\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script, timeout=60)
    if rc != 0:
        return {"ok": False, "error": stderr or "osascript failed"}
    notes = []
    for line in stdout.splitlines():
        if "|||" in line:
            nid, _, title = line.partition("|||")
            notes.append({"id": nid.strip(), "title": title.strip()})
    return {"ok": True, "count": len(notes), "notes": notes}


def cmd_get(args: argparse.Namespace) -> dict:
    nid = _escape(args.id)
    script = (
        'tell application "Notes"\n'
        f'  set n to first note whose id is "{nid}"\n'
        '  return (name of n) & "|||BODY|||" & (body of n)\n'
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "note not found"}
    title, _, body = stdout.partition("|||BODY|||")
    return {"ok": True, "id": args.id, "title": title.strip(), "body": body.strip()}


def cmd_create(args: argparse.Namespace) -> dict:
    title = _escape(args.title)
    body = _escape(args.body or "")
    script = (
        'tell application "Notes"\n'
        f'  set newNote to make new note with properties {{name:"{title}", body:"{body}"}}\n'
        "  return id of newNote\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "create failed"}
    return {"ok": True, "id": stdout.strip(), "title": args.title}


def cmd_search(args: argparse.Namespace) -> dict:
    query = _escape(args.query)
    script = (
        'tell application "Notes"\n'
        f'  set matches to (notes whose name contains "{query}" or body contains "{query}")\n'
        "  set out to {}\n"
        "  repeat with n in matches\n"
        '    set end of out to (id of n) & "|||" & (name of n)\n'
        "  end repeat\n"
        '  set AppleScript\'s text item delimiters to "\\n"\n'
        "  return out as text\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script, timeout=60)
    if rc != 0:
        return {"ok": False, "error": stderr or "search failed"}
    notes = []
    for line in stdout.splitlines():
        if "|||" in line:
            nid, _, title = line.partition("|||")
            notes.append({"id": nid.strip(), "title": title.strip()})
    return {"ok": True, "query": args.query, "count": len(notes), "notes": notes}


def cmd_update(args: argparse.Namespace) -> dict:
    nid = _escape(args.id)
    body = _escape(args.body)
    script = (
        'tell application "Notes"\n'
        f'  set n to first note whose id is "{nid}"\n'
        f'  set body of n to "{body}"\n'
        "  return id of n\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "update failed"}
    return {"ok": True, "id": stdout.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apple Notes bash interface")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    g = sub.add_parser("get")
    g.add_argument("--id", required=True)
    c = sub.add_parser("create")
    c.add_argument("--title", required=True)
    c.add_argument("--body", default="")
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    u = sub.add_parser("update")
    u.add_argument("--id", required=True)
    u.add_argument("--body", required=True)

    args = parser.parse_args(argv)
    handlers = {
        "list": cmd_list,
        "get": cmd_get,
        "create": cmd_create,
        "search": cmd_search,
        "update": cmd_update,
    }
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
