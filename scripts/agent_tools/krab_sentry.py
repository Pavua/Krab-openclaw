#!/usr/bin/env python3
"""Wave 45-C-tools — Sentry API через httpx.

Подкоманды: issues, events, resolve.
Token: SENTRY_AUTH_TOKEN (env). Если отсутствует — graceful 2 exit.
Default org: SENTRY_ORG_SLUG.

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

SCRIPT = "krab_sentry.py"
API_BASE = "https://sentry.io/api/0"
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


def _get_env() -> dict[str, str]:
    return {**os.environ, **_load_dotenv()}


def _get_token() -> str | None:
    return _get_env().get("SENTRY_AUTH_TOKEN") or None


def _get_default_org() -> str | None:
    return _get_env().get("SENTRY_ORG_SLUG") or None


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=DEFAULT_TIMEOUT,
    )


def _api_request(
    token: str, method: str, path: str, params: dict | None = None, json_body: dict | None = None
) -> dict:
    """Common HTTP wrapper. Возвращает {ok, data|error}."""
    try:
        with _client(token) as c:
            resp = c.request(method, path, params=params or {}, json=json_body)
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http error: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"HTTP {resp.status_code}",
            "body": resp.text[:500],
        }
    try:
        return {"ok": True, "data": resp.json()}
    except ValueError:
        return {"ok": True, "data": resp.text}


def cmd_issues(args: argparse.Namespace, token: str) -> dict:
    org = args.org or _get_default_org()
    if not org:
        return {"ok": False, "error": "org not specified (use --org or SENTRY_ORG_SLUG env)"}
    params: dict = {"limit": args.limit}
    if args.query:
        params["query"] = args.query
    res = _api_request(
        token,
        "GET",
        f"/projects/{org}/{args.project}/issues/",
        params=params,
    )
    if not res["ok"]:
        return res
    issues = res["data"] or []
    summary = [
        {
            "id": i.get("id"),
            "shortId": i.get("shortId"),
            "title": i.get("title"),
            "status": i.get("status"),
            "level": i.get("level"),
            "count": i.get("count"),
            "userCount": i.get("userCount"),
            "lastSeen": i.get("lastSeen"),
        }
        for i in (issues if isinstance(issues, list) else [])
    ]
    return {"ok": True, "org": org, "project": args.project, "count": len(summary), "issues": summary}


def cmd_events(args: argparse.Namespace, token: str) -> dict:
    res = _api_request(token, "GET", f"/issues/{args.issue}/events/", params={"limit": args.limit})
    if not res["ok"]:
        return res
    events = res["data"] or []
    summary = [
        {
            "id": e.get("id"),
            "eventID": e.get("eventID"),
            "message": e.get("message"),
            "platform": e.get("platform"),
            "dateCreated": e.get("dateCreated"),
        }
        for e in (events if isinstance(events, list) else [])
    ]
    return {"ok": True, "issue": args.issue, "count": len(summary), "events": summary}


def cmd_resolve(args: argparse.Namespace, token: str) -> dict:
    res = _api_request(
        token, "PUT", f"/issues/{args.issue}/", json_body={"status": "resolved"}
    )
    if not res["ok"]:
        return res
    return {"ok": True, "issue": args.issue, "status": "resolved", "data": res["data"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sentry API bash interface")
    parser.add_argument("--json", action="store_true", help="output JSON (default)")
    parser.add_argument("--org", help="org slug (override SENTRY_ORG_SLUG)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    iss = sub.add_parser("issues")
    iss.add_argument("--project", required=True)
    iss.add_argument("--limit", type=int, default=25)
    iss.add_argument("--query", default="is:unresolved")

    ev = sub.add_parser("events")
    ev.add_argument("--issue", required=True)
    ev.add_argument("--limit", type=int, default=10)

    res_p = sub.add_parser("resolve")
    res_p.add_argument("--issue", required=True)

    args = parser.parse_args(argv)

    token = _get_token()
    if not token:
        emit_json(
            {
                "ok": False,
                "error": "SENTRY_AUTH_TOKEN not set",
                "hint": "create token at https://sentry.io/settings/account/api/auth-tokens/",
            },
            SCRIPT,
            sys.argv[1:],
        )
        return 2

    handlers = {"issues": cmd_issues, "events": cmd_events, "resolve": cmd_resolve}
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
