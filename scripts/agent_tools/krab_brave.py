#!/usr/bin/env python3
"""Wave 45-C-tools — Brave Search API через httpx.

Подкоманды: search.
Token: BRAVE_SEARCH_API_KEY (env / .env).
JSON output: top N результатов.

exit codes: 0 ok / 1 error / 2 missing token.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import emit_error, emit_json  # noqa: E402

SCRIPT = "krab_brave.py"
API_BASE = "https://api.search.brave.com/res/v1"
DEFAULT_TIMEOUT = 30.0
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> dict[str, str]:
    """Минималистичный .env loader."""
    env_path = REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _get_token() -> str | None:
    env = {**os.environ, **_load_dotenv()}
    return env.get("BRAVE_SEARCH_API_KEY") or None


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": token,
        },
        timeout=DEFAULT_TIMEOUT,
    )


def cmd_search(args: argparse.Namespace, token: str) -> dict:
    params = {"q": args.query, "count": min(max(args.count, 1), 20)}
    try:
        with _client(token) as c:
            resp = c.get("/web/search", params=params)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http error: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code}", "body": resp.text[:500]}
    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "error": "invalid JSON response"}

    web = body.get("web") or {}
    results_raw = web.get("results") or []
    results = [
        {
            "title": r.get("title"),
            "url": r.get("url"),
            "description": r.get("description"),
            "age": r.get("age"),
        }
        for r in results_raw[: args.count]
    ]
    return {"ok": True, "query": args.query, "count": len(results), "results": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Brave Search API bash interface")
    parser.add_argument("--json", action="store_true", help="output JSON (default)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("--query", required=True)
    s.add_argument("--count", type=int, default=10)

    args = parser.parse_args(argv)

    token = _get_token()
    if not token:
        emit_json(
            {
                "ok": False,
                "error": "BRAVE_SEARCH_API_KEY not set",
                "hint": "set BRAVE_SEARCH_API_KEY in .env or env",
            },
            SCRIPT,
            sys.argv[1:],
        )
        return 2

    handlers = {"search": cmd_search}
    handler = handlers.get(args.cmd)
    if handler is None:
        return emit_error(f"unknown subcommand: {args.cmd}", SCRIPT, sys.argv[1:])

    try:
        result = handler(args, token)
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
