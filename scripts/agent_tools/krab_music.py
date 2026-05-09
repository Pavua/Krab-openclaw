#!/usr/bin/env python3
"""Wave 44-T-apple-apps — Apple Music.app via osascript.

Subcommands: play, pause, next, current.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_music.py"


def _run_osa(script: str, timeout: int = 15) -> tuple[int, str, str]:
    proc = subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def cmd_play(args: argparse.Namespace) -> dict:
    if args.playlist:
        target = _escape(args.playlist)
        script = (
            'tell application "Music"\n'
            f'  play playlist "{target}"\n'
            '  return "playing playlist"\n'
            "end tell"
        )
    elif args.track:
        target = _escape(args.track)
        script = (
            'tell application "Music"\n'
            f'  play (first track whose name is "{target}")\n'
            '  return "playing track"\n'
            "end tell"
        )
    elif args.artist:
        target = _escape(args.artist)
        script = (
            'tell application "Music"\n'
            f'  play (first track whose artist is "{target}")\n'
            '  return "playing artist"\n'
            "end tell"
        )
    elif args.album:
        target = _escape(args.album)
        script = (
            'tell application "Music"\n'
            f'  play (first track whose album is "{target}")\n'
            '  return "playing album"\n'
            "end tell"
        )
    else:
        script = 'tell application "Music"\n  play\n  return "resumed"\nend tell'

    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "play failed"}
    return {"ok": True, "action": stdout.strip()}


def cmd_pause(_args: argparse.Namespace) -> dict:
    rc, _stdout, stderr = _run_osa('tell application "Music" to pause')
    if rc != 0:
        return {"ok": False, "error": stderr or "pause failed"}
    return {"ok": True, "action": "paused"}


def cmd_next(_args: argparse.Namespace) -> dict:
    rc, _stdout, stderr = _run_osa('tell application "Music" to next track')
    if rc != 0:
        return {"ok": False, "error": stderr or "next failed"}
    return {"ok": True, "action": "next"}


def cmd_current(_args: argparse.Namespace) -> dict:
    script = (
        'tell application "Music"\n'
        "  if player state is playing or player state is paused then\n"
        "    set t to current track\n"
        '    return (name of t) & "|||" & (artist of t) & "|||" & (album of t) & '
        '"|||" & ((player state) as string)\n'
        "  else\n"
        '    return "STOPPED"\n'
        "  end if\n"
        "end tell"
    )
    rc, stdout, stderr = _run_osa(script)
    if rc != 0:
        return {"ok": False, "error": stderr or "current failed"}
    if stdout.strip() == "STOPPED" or "|||" not in stdout:
        return {"ok": True, "playing": False}
    parts = stdout.split("|||")
    return {
        "ok": True,
        "playing": True,
        "track": parts[0].strip() if len(parts) > 0 else "",
        "artist": parts[1].strip() if len(parts) > 1 else "",
        "album": parts[2].strip() if len(parts) > 2 else "",
        "state": parts[3].strip() if len(parts) > 3 else "",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apple Music bash interface")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("play")
    p.add_argument("--track", default=None)
    p.add_argument("--playlist", default=None)
    p.add_argument("--artist", default=None)
    p.add_argument("--album", default=None)
    sub.add_parser("pause")
    sub.add_parser("next")
    sub.add_parser("current")

    args = parser.parse_args(argv)
    handlers = {
        "play": cmd_play,
        "pause": cmd_pause,
        "next": cmd_next,
        "current": cmd_current,
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
