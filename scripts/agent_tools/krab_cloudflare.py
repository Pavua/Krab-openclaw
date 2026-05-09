#!/usr/bin/env python3
"""Wave 45-C-tools — Cloudflare API через httpx.

Подкоманды: zones list, dns list, kv list-namespaces, worker list.
Token: CLOUDFLARE_API_TOKEN (env). Read-only by default; mutating ops
требуют --confirm flag (на случай будущих расширений).

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

SCRIPT = "krab_cloudflare.py"
API_BASE = "https://api.cloudflare.com/client/v4"
DEFAULT_TIMEOUT = 30.0
REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> dict[str, str]:
    """Минималистичный .env loader (без python-dotenv)."""
    out: dict[str, str] = {}
    if os.environ.get("KRAB_TOOLS_DISABLE_DOTENV"):
        return out
    env_path = REPO_ROOT / ".env"
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
    return env.get("CLOUDFLARE_API_TOKEN") or None


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=API_BASE,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        timeout=DEFAULT_TIMEOUT,
    )


def _api_get(token: str, path: str, params: dict | None = None) -> dict:
    """GET wrapper. Возвращает {ok, data|error}."""
    try:
        with _client(token) as c:
            resp = c.get(path, params=params or {})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"http error: {type(exc).__name__}: {exc}"}
    if resp.status_code >= 400:
        return {
            "ok": False,
            "error": f"HTTP {resp.status_code}",
            "body": resp.text[:500],
        }
    try:
        body = resp.json()
    except ValueError:
        return {"ok": False, "error": "invalid JSON response"}
    if not body.get("success", False):
        return {"ok": False, "error": "cloudflare api error", "errors": body.get("errors")}
    return {"ok": True, "data": body.get("result"), "result_info": body.get("result_info")}


def cmd_zones_list(args: argparse.Namespace, token: str) -> dict:
    res = _api_get(token, "/zones", params={"per_page": args.limit})
    if not res["ok"]:
        return res
    zones = res["data"] or []
    summary = [
        {"id": z.get("id"), "name": z.get("name"), "status": z.get("status")}
        for z in zones
    ]
    return {"ok": True, "count": len(summary), "zones": summary}


def cmd_dns_list(args: argparse.Namespace, token: str) -> dict:
    res = _api_get(token, f"/zones/{args.zone}/dns_records", params={"per_page": args.limit})
    if not res["ok"]:
        return res
    records = res["data"] or []
    summary = [
        {
            "id": r.get("id"),
            "type": r.get("type"),
            "name": r.get("name"),
            "content": r.get("content"),
            "proxied": r.get("proxied"),
            "ttl": r.get("ttl"),
        }
        for r in records
    ]
    return {"ok": True, "zone": args.zone, "count": len(summary), "records": summary}


def cmd_kv_list_namespaces(args: argparse.Namespace, token: str) -> dict:
    res = _api_get(
        token,
        f"/accounts/{args.account}/storage/kv/namespaces",
        params={"per_page": args.limit},
    )
    if not res["ok"]:
        return res
    ns = res["data"] or []
    summary = [{"id": n.get("id"), "title": n.get("title")} for n in ns]
    return {"ok": True, "account": args.account, "count": len(summary), "namespaces": summary}


def cmd_worker_list(args: argparse.Namespace, token: str) -> dict:
    res = _api_get(token, f"/accounts/{args.account}/workers/scripts")
    if not res["ok"]:
        return res
    workers = res["data"] or []
    summary = [
        {
            "id": w.get("id"),
            "created_on": w.get("created_on"),
            "modified_on": w.get("modified_on"),
        }
        for w in workers
    ]
    return {"ok": True, "account": args.account, "count": len(summary), "workers": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cloudflare API bash interface")
    parser.add_argument("--json", action="store_true", help="output JSON (default)")
    parser.add_argument(
        "--confirm", action="store_true", help="confirm mutating operations (reserved)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    zones = sub.add_parser("zones")
    zones_sub = zones.add_subparsers(dest="action", required=True)
    zl = zones_sub.add_parser("list")
    zl.add_argument("--limit", type=int, default=50)

    dns = sub.add_parser("dns")
    dns_sub = dns.add_subparsers(dest="action", required=True)
    dl = dns_sub.add_parser("list")
    dl.add_argument("--zone", required=True)
    dl.add_argument("--limit", type=int, default=100)

    kv = sub.add_parser("kv")
    kv_sub = kv.add_subparsers(dest="action", required=True)
    kvls = kv_sub.add_parser("list-namespaces")
    kvls.add_argument("--account", required=True)
    kvls.add_argument("--limit", type=int, default=50)

    worker = sub.add_parser("worker")
    w_sub = worker.add_subparsers(dest="action", required=True)
    wl = w_sub.add_parser("list")
    wl.add_argument("--account", required=True)

    args = parser.parse_args(argv)

    token = _get_token()
    if not token:
        emit_json(
            {
                "ok": False,
                "error": "CLOUDFLARE_API_TOKEN not set",
                "hint": "export CLOUDFLARE_API_TOKEN=... or add to .env",
            },
            SCRIPT,
            sys.argv[1:],
        )
        return 2

    handlers = {
        ("zones", "list"): cmd_zones_list,
        ("dns", "list"): cmd_dns_list,
        ("kv", "list-namespaces"): cmd_kv_list_namespaces,
        ("worker", "list"): cmd_worker_list,
    }
    key = (args.cmd, args.action)
    handler = handlers.get(key)
    if handler is None:
        return emit_error(f"unknown subcommand: {key}", SCRIPT, sys.argv[1:])

    try:
        result = handler(args, token)
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])

    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
