#!/usr/bin/env python3
"""Snapshot endpoint paths и handler-имена для regression detection.

Использование:
    snapshot_endpoints_commands.py             # печать stats + diff vs baselines
    snapshot_endpoints_commands.py --diff      # diff vs baselines (exit 1 если есть)
    snapshot_endpoints_commands.py --write     # перезаписать baselines из live runtime

Источник истины: live Krab Owner panel на http://127.0.0.1:8080.
Baseline-файлы:
    tests/fixtures/api_endpoints_baseline.json
    tests/fixtures/commands_baseline.json
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
ENDPOINTS_BASELINE = FIXTURES_DIR / "api_endpoints_baseline.json"
COMMANDS_BASELINE = FIXTURES_DIR / "commands_baseline.json"

OWNER_PANEL_BASE = "http://127.0.0.1:8080"
ENDPOINTS_URL = f"{OWNER_PANEL_BASE}/api/endpoints"
COMMANDS_URL = f"{OWNER_PANEL_BASE}/api/commands"

DEFAULT_SESSION = "Session 24"


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_live_endpoints() -> list[str]:
    payload = _http_get_json(ENDPOINTS_URL)
    items = payload.get("endpoints") or []
    paths = {item["path"] for item in items if isinstance(item, dict) and item.get("path")}
    return sorted(paths)


def _fetch_live_commands() -> list[str]:
    payload = _http_get_json(COMMANDS_URL)
    items = payload.get("commands") or []
    names = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        names.add(name if name.startswith("!") else f"!{name}")
    return sorted(names)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _load_baseline(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_baseline(path: Path, *, key: str, items: list[str], session: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_at": _now_iso(),
        "session": session,
        "count": len(items),
        key: items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _diff(baseline: list[str], live: list[str]) -> tuple[list[str], list[str]]:
    base = set(baseline)
    cur = set(live)
    added = sorted(cur - base)
    removed = sorted(base - cur)
    return added, removed


def _print_diff(label: str, added: list[str], removed: list[str]) -> bool:
    if not added and not removed:
        print(f"  {label}: no diff")
        return False
    if added:
        print(f"  {label} ADDED ({len(added)}):")
        for x in added:
            print(f"    + {x}")
    if removed:
        print(f"  {label} REMOVED ({len(removed)}):")
        for x in removed:
            print(f"    - {x}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="overwrite baseline fixtures from live runtime")
    group.add_argument("--diff", action="store_true", help="exit 1 if live differs from baseline")
    parser.add_argument("--session", default=DEFAULT_SESSION, help="session label stored in baseline")
    args = parser.parse_args(argv)

    try:
        live_endpoints = _fetch_live_endpoints()
        live_commands = _fetch_live_commands()
    except Exception as exc:
        print(f"ERROR: failed to fetch live data: {exc}", file=sys.stderr)
        return 2

    print(f"Live: {len(live_endpoints)} endpoints, {len(live_commands)} commands")

    if args.write:
        _write_baseline(ENDPOINTS_BASELINE, key="endpoints", items=live_endpoints, session=args.session)
        _write_baseline(COMMANDS_BASELINE, key="commands", items=live_commands, session=args.session)
        print(f"Wrote {ENDPOINTS_BASELINE}")
        print(f"Wrote {COMMANDS_BASELINE}")
        return 0

    base_ep = _load_baseline(ENDPOINTS_BASELINE)
    base_cmd = _load_baseline(COMMANDS_BASELINE)

    if base_ep is None or base_cmd is None:
        print("ERROR: baseline fixtures missing — run with --write first", file=sys.stderr)
        return 2

    print(f"Baseline endpoints: {base_ep['count']} (snapshot_at={base_ep['snapshot_at']}, session={base_ep['session']})")
    print(f"Baseline commands:  {base_cmd['count']} (snapshot_at={base_cmd['snapshot_at']}, session={base_cmd['session']})")

    ep_added, ep_removed = _diff(base_ep["endpoints"], live_endpoints)
    cmd_added, cmd_removed = _diff(base_cmd["commands"], live_commands)

    print("Diff:")
    has_ep = _print_diff("endpoints", ep_added, ep_removed)
    has_cmd = _print_diff("commands", cmd_added, cmd_removed)
    has_diff = has_ep or has_cmd

    if args.diff and has_diff:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
