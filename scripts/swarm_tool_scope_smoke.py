# -*- coding: utf-8 -*-
"""
scripts/swarm_tool_scope_smoke.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Smoke-тест per-team tool allowlist (commit 8d58c5d).

1. Собирает tool manifest: сначала пытается через live `mcp_manager`
   (полный путь как в `_openclaw_completion_once`); если live MCP недоступно —
   строит synthetic manifest, точно совпадающий по форме имён ({server}__{tool})
   с реальными yung-nagato / p0lrd + нативными (web_search / peekaboo / tor_fetch).

2. Для каждой команды (traders, coders, analysts, creative, unknown_team):
   выставляет ContextVar `_swarm_team_ctx`, фильтрует manifest через
   `filter_tools_for_team`, печатает отчёт (allowed / blocked / counts),
   сбрасывает ContextVar.

3. Печатает итог в формате markdown-блоков — пригодно для docs/SWARM_TOOL_SCOPE_SMOKE.md.

Запуск:
    venv/bin/python scripts/swarm_tool_scope_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Мягко: не поднимаем Pyrogram/бот — только манифест и allowlist.
os.environ.setdefault("KRAB_SMOKE_MODE", "1")

from src.core.swarm_tool_allowlist import (  # noqa: E402
    TEAM_TOOL_ALLOWLIST,
    _BASE_ALLOWLIST,
    filter_tools_for_team,
    get_current_team,
    reset_current_team,
    set_current_team,
)


def _synthetic_manifest() -> list[dict]:
    """Синтетический manifest под формат mcp_client.get_tool_manifest()."""
    tools: list[dict] = []

    # yung-nagato MCP (owner-main krab)
    for t in (
        "krab_memory_search",
        "krab_memory_stats",
        "krab_restart_gateway",
        "krab_run_tests",
        "krab_status",
        "krab_tail_logs",
        "telegram_download_media",
        "telegram_edit_message",
        "telegram_get_chat_history",
        "telegram_get_dialogs",
        "telegram_search",
        "telegram_send_message",
        "telegram_transcribe_voice",
    ):
        tools.append(
            {"type": "function", "function": {"name": f"yung-nagato__{t}", "description": t, "parameters": {}}}
        )

    # p0lrd MCP (второй owner)
    for t in (
        "krab_memory_search",
        "krab_run_tests",
        "krab_tail_logs",
        "telegram_send_message",
    ):
        tools.append(
            {"type": "function", "function": {"name": f"p0lrd__{t}", "description": t, "parameters": {}}}
        )

    # filesystem MCP
    for t in ("read_file", "write_file", "list_directory", "search_files"):
        tools.append(
            {"type": "function", "function": {"name": f"filesystem__{t}", "description": t, "parameters": {}}}
        )

    # git MCP
    for t in ("status", "diff", "log", "add", "commit"):
        tools.append(
            {"type": "function", "function": {"name": f"git__{t}", "description": t, "parameters": {}}}
        )

    # Нативные (добавляются в mcp_client.get_tool_manifest напрямую)
    for t in ("web_search", "peekaboo", "tor_fetch"):
        tools.append(
            {"type": "function", "function": {"name": t, "description": t, "parameters": {}}}
        )

    return tools


async def _live_manifest() -> list[dict] | None:
    """Попытка собрать live manifest — иначе None."""
    try:
        from src.mcp_client import mcp_manager  # type: ignore

        # Не инициируем connect — если sessions пусты, вернёт только нативные.
        manifest = await mcp_manager.get_tool_manifest()
        return manifest
    except Exception as exc:  # noqa: BLE001
        print(f"[live manifest unavailable] {exc!r}", file=sys.stderr)
        return None


def _names(manifest: list[dict]) -> list[str]:
    return [str(t.get("function", {}).get("name", "")) for t in manifest]


def _run_report(manifest: list[dict]) -> None:
    total = len(manifest)
    print(f"\n## Full manifest ({total} tools)\n")
    for n in sorted(_names(manifest)):
        print(f"- `{n}`")

    teams = ["traders", "coders", "analysts", "creative", "unknown_team"]
    print("\n---\n")
    print("## Per-team filter results\n")

    for team in teams:
        token = set_current_team(team)
        try:
            assert get_current_team() == team
            filtered = filter_tools_for_team(manifest, team)
            allowed = sorted(_names(filtered))
            blocked = sorted(set(_names(manifest)) - set(allowed))
            allowlist = TEAM_TOOL_ALLOWLIST.get(team)
            print(f"### Team: `{team}`")
            if allowlist is None:
                print(f"- whitelist: **None** → passthrough (backward-compat)")
            else:
                eff = sorted(allowlist | _BASE_ALLOWLIST)
                print(f"- whitelist ({len(eff)}): {', '.join(f'`{t}`' for t in eff)}")
            print(f"- allowed: **{len(allowed)}** / {total}")
            for n in allowed:
                print(f"  - `{n}`")
            print(f"- blocked: **{len(blocked)}**")
            for n in blocked[:15]:
                print(f"  - `{n}`")
            if len(blocked) > 15:
                print(f"  - … ({len(blocked) - 15} more)")
            print()
        finally:
            reset_current_team(token)
            assert get_current_team() is None, "ContextVar leaked!"

    print("---\n")
    print("## Verdict\n")
    # Sanity checks
    checks = []
    t_traders = _names(filter_tools_for_team(manifest, "traders"))
    checks.append(("traders sees web_search", any(n == "web_search" for n in t_traders)))
    checks.append(
        ("traders sees krab_memory_search (any server prefix)",
         any(n.endswith("__krab_memory_search") or n == "krab_memory_search" for n in t_traders)),
    )
    checks.append(
        ("traders does NOT see krab_run_tests",
         not any(n.endswith("__krab_run_tests") for n in t_traders)),
    )
    checks.append(
        ("traders does NOT see filesystem__read_file",
         "filesystem__read_file" not in t_traders),
    )

    t_coders = _names(filter_tools_for_team(manifest, "coders"))
    checks.append(
        ("coders sees krab_run_tests (some server)",
         any(n.endswith("__krab_run_tests") for n in t_coders)),
    )
    checks.append(
        ("coders does NOT see telegram_send_message",
         not any(n.endswith("__telegram_send_message") for n in t_coders)),
    )

    t_analysts = _names(filter_tools_for_team(manifest, "analysts"))
    checks.append(
        ("analysts sees telegram_search",
         any(n.endswith("__telegram_search") for n in t_analysts)),
    )

    t_creative = _names(filter_tools_for_team(manifest, "creative"))
    checks.append(
        ("creative sees telegram_send_message",
         any(n.endswith("__telegram_send_message") for n in t_creative)),
    )

    t_unknown = _names(filter_tools_for_team(manifest, "unknown_team"))
    checks.append(
        ("unknown_team passthrough (full manifest)",
         len(t_unknown) == len(manifest)),
    )

    all_ok = True
    for label, ok in checks:
        mark = "✅" if ok else "❌"
        if not ok:
            all_ok = False
        print(f"- {mark} {label}")

    print()
    print(f"**Overall:** {'PASS — allowlist filter works on real manifest shape' if all_ok else 'FAIL — see above'}")


async def main() -> None:
    print("# Swarm tool scope smoke\n")
    live = await _live_manifest()
    if live and len(live) > 15:
        print(f"**Source:** live `mcp_manager.get_tool_manifest()` — {len(live)} tools")
        manifest = live
    else:
        n_live = len(live) if live is not None else 0
        print(
            f"**Source:** synthetic manifest — live manifest had only {n_live} tools "
            "(MCP SSE sessions not initialised in smoke harness; "
            "synthetic mimics real `{server}__{tool}` shape from yung-nagato / p0lrd / filesystem / git)"
        )
        manifest = _synthetic_manifest()
    _run_report(manifest)


if __name__ == "__main__":
    asyncio.run(main())
