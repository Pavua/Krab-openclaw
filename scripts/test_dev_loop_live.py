"""Live verification of 5 dev-loop MCP tools (commit b817c31).

Directly imports async functions from mcp-servers/telegram/server.py — bypasses
MCP protocol. Destructive tools (sentry_resolve, deploy_and_verify) are gated
behind --dangerous flag.

Usage:
    venv/bin/python scripts/test_dev_loop_live.py            # safe tests only
    venv/bin/python scripts/test_dev_loop_live.py --dangerous # include deploy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJECT_ROOT / "mcp-servers" / "telegram"))

# Load .env into os.environ so SENTRY_AUTH_TOKEN is visible
_env_path = _PROJECT_ROOT / ".env"
if _env_path.exists():
    import os

    for raw in _env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from server import (  # noqa: E402
    _DeployVerifyInput,
    _LogTailInput,
    _RunE2EInput,
    _SentryResolveInput,
    _SentryStatusInput,
    krab_deploy_and_verify,
    krab_log_tail,
    krab_run_e2e,
    krab_sentry_resolve,
    krab_sentry_status,
)


def _preview(s: str, n: int = 120) -> str:
    s = s.replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


async def _run_one(name: str, coro):
    t0 = time.monotonic()
    try:
        out = await coro
        dur = round(time.monotonic() - t0, 2)
        try:
            data = json.loads(out)
            ok = bool(data.get("ok"))
        except Exception:
            data, ok = None, False
        verdict = "OK" if ok else "FAIL"
        return {
            "name": name,
            "verdict": verdict,
            "duration_s": dur,
            "sample": _preview(out),
            "raw": out,
            "parsed": data,
        }
    except Exception as exc:
        return {
            "name": name,
            "verdict": "FAIL",
            "duration_s": round(time.monotonic() - t0, 2),
            "sample": f"EXC: {exc!r}",
            "raw": "",
            "parsed": None,
        }


async def main(dangerous: bool) -> int:
    results = []

    # 1. sentry_status
    results.append(
        await _run_one(
            "krab_sentry_status",
            krab_sentry_status(
                _SentryStatusInput(project="python-fastapi", statsPeriod="24h", limit=5)
            ),
        )
    )

    # 2. sentry_resolve — dry-run with bogus shortId (signature verify, expects
    # resolved_count=0 + failed=[{not_found}]). We use a non-existent ID so
    # nothing gets actually resolved.
    results.append(
        await _run_one(
            "krab_sentry_resolve",
            krab_sentry_resolve(
                _SentryResolveInput(
                    shortIds=["PYTHON-FASTAPI-DOES-NOT-EXIST-9999"],
                    project="python-fastapi",
                )
            ),
        )
    )

    # 3. log_tail — pattern="command_blocklist_skip" per brief
    results.append(
        await _run_one(
            "krab_log_tail",
            krab_log_tail(_LogTailInput(pattern="command_blocklist_skip", level="all", n=5)),
        )
    )
    # also broad sanity check
    results.append(
        await _run_one(
            "krab_log_tail[broad]",
            krab_log_tail(_LogTailInput(pattern=".*", level="warn+error", n=3)),
        )
    )

    # 4. run_e2e — actually executes the live e2e script
    results.append(
        await _run_one(
            "krab_run_e2e",
            krab_run_e2e(_RunE2EInput(chat_id=312322764, force=True)),
        )
    )

    # 5. deploy_and_verify — gated
    if dangerous:
        results.append(
            await _run_one(
                "krab_deploy_and_verify",
                krab_deploy_and_verify(_DeployVerifyInput(skip_tests=True)),
            )
        )
    else:
        results.append(
            {
                "name": "krab_deploy_and_verify",
                "verdict": "SKIP",
                "duration_s": 0,
                "sample": "skipped (dangerous) — pass --dangerous to run",
                "raw": "",
                "parsed": None,
            }
        )

    # Pretty print
    print("\n=== DEV-LOOP LIVE VERIFICATION ===\n")
    print(f"{'TOOL':<28} {'VERDICT':<8} {'DUR':<8} SAMPLE")
    print("-" * 100)
    for r in results:
        print(f"{r['name']:<28} {r['verdict']:<8} {r['duration_s']:<8} {r['sample']}")

    # Detailed dump
    print("\n=== DETAILS ===\n")
    for r in results:
        print(f"--- {r['name']} [{r['verdict']}] ---")
        if r["parsed"] is not None:
            keys = list(r["parsed"].keys()) if isinstance(r["parsed"], dict) else []
            print(f"keys: {keys}")
        print(r["raw"][:500])
        print()

    # Exit non-zero if any FAIL
    bad = [r for r in results if r["verdict"] == "FAIL"]
    return 0 if not bad else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dangerous",
        action="store_true",
        help="Actually run krab_deploy_and_verify (push+restart!). Default skipped.",
    )
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.dangerous)))
