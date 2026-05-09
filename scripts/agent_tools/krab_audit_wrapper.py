#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Wave 44-S-safety-net: unified audit wrapper для всех agent_tools/* скриптов.

Логирует все вызовы в JSONL формат:
  {ts, tool, args, exit_code, ppid, ppid_chain, duration_ms}

Usage (opt-in для existing scripts через --audit flag):
    python krab_audit_wrapper.py --tool krab_send_to_swarm \\
        --target scripts/agent_tools/krab_send_to_swarm.py \\
        -- --text "hello"

или programmatically:
    from krab_audit_wrapper import audit_log
    audit_log(tool="my_tool", args=[...], exit_code=0, duration_ms=42)

Backwards compat: existing скрипты продолжают работать без изменений;
этот wrapper — additive. Чтобы включить аудит для существующего скрипта,
запускайте его через `python krab_audit_wrapper.py --tool X --target Y -- ARGS`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

AUDIT_PATH = Path(
    os.environ.get(
        "KRAB_AGENT_AUDIT_PATH",
        "/Users/pablito/.openclaw/krab_runtime_state/agent_audit.jsonl",
    )
)
AUDIT_MAX_BYTES = 10 * 1024 * 1024  # 10MB


def _ppid_chain(max_depth: int = 5) -> list[int]:
    """Return chain of parent PIDs up to max_depth — для traceability."""
    chain: list[int] = []
    pid = os.getppid()
    for _ in range(max_depth):
        if pid <= 1:
            break
        chain.append(pid)
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
            )
            pid = int(out.stdout.strip() or 0)
        except (subprocess.SubprocessError, ValueError):
            break
    return chain


def _rotate_if_needed() -> None:
    try:
        if AUDIT_PATH.exists() and AUDIT_PATH.stat().st_size > AUDIT_MAX_BYTES:
            rotated = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".1")
            try:
                rotated.unlink()
            except FileNotFoundError:
                pass
            AUDIT_PATH.rename(rotated)
    except OSError:
        pass


def audit_log(
    *,
    tool: str,
    args: list[str] | None = None,
    exit_code: int = 0,
    duration_ms: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    """Записать audit-запись в jsonl файл. Никогда не raise."""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        record = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool,
            "args": args or [],
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "ppid_chain": _ppid_chain(),
        }
        if extra:
            record.update(extra)
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # Audit failure НЕ должен ломать tool execution
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Wave 44-S-safety-net audit wrapper",
        allow_abbrev=False,
    )
    parser.add_argument("--tool", required=True, help="Tool name for audit log")
    parser.add_argument(
        "--target",
        required=True,
        help="Path to script to execute (Python or shell)",
    )
    args, passthrough = parser.parse_known_args()

    target = Path(args.target)
    if not target.exists():
        audit_log(
            tool=args.tool,
            args=passthrough,
            exit_code=127,
            extra={"error": f"target_not_found: {target}"},
        )
        print(json.dumps({"ok": False, "error": "target_not_found"}), file=sys.stderr)
        return 127

    # Strip leading "--" from passthrough (argparse-style separator)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    if target.suffix == ".py":
        cmd = [sys.executable, str(target), *passthrough]
    else:
        cmd = [str(target), *passthrough]

    start = time.monotonic()
    try:
        result = subprocess.run(cmd, check=False)
        rc = result.returncode
    except (OSError, subprocess.SubprocessError) as exc:
        audit_log(
            tool=args.tool,
            args=passthrough,
            exit_code=255,
            extra={"error": f"subprocess_error: {exc!r}"},
        )
        return 255
    duration_ms = int((time.monotonic() - start) * 1000)
    audit_log(tool=args.tool, args=passthrough, exit_code=rc, duration_ms=duration_ms)
    return rc


if __name__ == "__main__":
    sys.exit(main())
