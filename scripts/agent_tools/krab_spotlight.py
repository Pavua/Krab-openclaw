#!/usr/bin/env python3
"""Wave 44-T-apple-apps — Spotlight search via mdfind.

Subcommand: search.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_spotlight.py"


def cmd_search(args: argparse.Namespace) -> dict:
    cmd = ["/usr/bin/mdfind"]
    if args.only_in:
        cmd.extend(["-onlyin", args.only_in])
    cmd.append(args.query)
    proc = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or "mdfind failed"}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    limit = args.limit or 25
    return {
        "ok": True,
        "query": args.query,
        "count": len(lines),
        "results": lines[:limit],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Spotlight (mdfind) search")
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--only-in", default=None, help="restrict search to a directory")
    s.add_argument("--limit", type=int, default=25)

    args = parser.parse_args(argv)
    try:
        result = cmd_search(args) if args.cmd == "search" else {"ok": False, "error": "unknown"}
    except subprocess.TimeoutExpired:
        return emit_error("mdfind timeout", SCRIPT, sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
