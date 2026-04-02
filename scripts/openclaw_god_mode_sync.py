#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синхронизирует persistent God Mode для OpenClaw в runtime-файлах.

Зачем нужен:
- начиная с OpenClaw 2026.4.x unrestricted exec собирается уже не только из
  `openclaw.json`, но и из отдельного approval-store `exec-approvals.json`;
- ручное редактирование только `tools.exec` больше не гарантирует, что агент
  реально сможет выполнять команды без `allowlist miss`;
- launcher должен уметь идемпотентно вернуть рабочее состояние перед стартом.

Связи:
- вызывается из `new start_krab.command`;
- может запускаться вручную для быстрой починки runtime после обновлений.
"""

from __future__ import annotations

import argparse
import json
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    """Читает JSON-объект или возвращает пустой словарь."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Пишет JSON в человекочитаемом виде."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _ensure_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    """Гарантирует словарь по ключу."""
    value = parent.get(key)
    if not isinstance(value, dict):
        value = {}
        parent[key] = value
    return value


def _ensure_list(parent: dict[str, Any], key: str) -> list[Any]:
    """Гарантирует список по ключу."""
    value = parent.get(key)
    if not isinstance(value, list):
        value = []
        parent[key] = value
    return value


def _set_if_different(target: dict[str, Any], key: str, value: Any, changed: dict[str, Any], path: str) -> None:
    """Записывает значение только если оно реально отличается."""
    previous = target.get(key)
    if previous != value:
        target[key] = value
        changed[path] = {"from": previous, "to": value}


def _ensure_agent(payload: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """Возвращает запись агента из `agents.list`, создавая её при отсутствии."""
    agents = _ensure_dict(payload, "agents")
    agent_list = _ensure_list(agents, "list")
    for item in agent_list:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == agent_id:
            return item

    created = {"id": agent_id}
    agent_list.append(created)
    return created


def sync_openclaw_json(openclaw_path: Path, *, agent_id: str = "main") -> dict[str, Any]:
    """Возвращает `openclaw.json` в состояние God Mode без sandbox/approval prompt."""
    payload = _read_json(openclaw_path)
    changed: dict[str, Any] = {}

    meta = _ensure_dict(payload, "meta")
    tools = _ensure_dict(payload, "tools")
    exec_cfg = _ensure_dict(tools, "exec")
    approvals = _ensure_dict(payload, "approvals")
    approvals_exec = _ensure_dict(approvals, "exec")
    agent = _ensure_agent(payload, agent_id)
    agent_tools = _ensure_dict(agent, "tools")

    _set_if_different(exec_cfg, "host", "gateway", changed, "tools.exec.host")
    _set_if_different(exec_cfg, "security", "full", changed, "tools.exec.security")
    _set_if_different(exec_cfg, "ask", "off", changed, "tools.exec.ask")
    _set_if_different(exec_cfg, "notifyOnExit", True, changed, "tools.exec.notifyOnExit")
    _set_if_different(
        exec_cfg,
        "notifyOnExitEmptySuccess",
        True,
        changed,
        "tools.exec.notifyOnExitEmptySuccess",
    )
    _set_if_different(approvals_exec, "enabled", False, changed, "approvals.exec.enabled")
    _set_if_different(agent_tools, "profile", "full", changed, f"agents.list[{agent_id}].tools.profile")

    if changed:
        meta["lastTouchedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _write_json(openclaw_path, payload)

    return {
        "path": str(openclaw_path),
        "changed": bool(changed),
        "changes": changed,
    }


def _default_exec_approvals_payload(approvals_path: Path) -> dict[str, Any]:
    """Создаёт минимальный approval-store, если файл отсутствует или повреждён."""
    home = Path.home()
    return {
        "version": 1,
        "socket": {
            "path": str(home / ".openclaw" / "exec-approvals.sock"),
            "token": secrets.token_urlsafe(24),
        },
        "defaults": {
            "security": "allowlist",
            "autoAllowSkills": True,
        },
        "agents": {},
        "_meta": {
            "createdBy": "openclaw_god_mode_sync",
            "pathHint": str(approvals_path),
        },
    }


def _normalize_allowlist_entry(entry: Any) -> dict[str, Any] | None:
    """Возвращает валидную allowlist-запись или `None`."""
    if not isinstance(entry, dict):
        return None
    pattern = str(entry.get("pattern") or "").strip()
    if not pattern:
        return None
    normalized = dict(entry)
    normalized["pattern"] = pattern
    return normalized


def _ensure_wildcard_allowlist(agent_payload: dict[str, Any]) -> bool:
    """Гарантирует wildcard-запись `*` для конкретного агента."""
    allowlist = agent_payload.get("allowlist")
    if not isinstance(allowlist, list):
        allowlist = []
        agent_payload["allowlist"] = allowlist

    normalized_entries: list[dict[str, Any]] = []
    for raw in allowlist:
        normalized = _normalize_allowlist_entry(raw)
        if normalized is not None:
            normalized_entries.append(normalized)
    agent_payload["allowlist"] = normalized_entries

    if any(str(item.get("pattern") or "").strip() == "*" for item in normalized_entries):
        return False

    normalized_entries.append(
        {
            "pattern": "*",
            "id": str(uuid.uuid4()),
            "lastUsedAt": int(time.time() * 1000),
            "source": "god-mode-sync",
        }
    )
    return True


def sync_exec_approvals(approvals_path: Path, *, agent_ids: tuple[str, ...] = ("main", "*")) -> dict[str, Any]:
    """Гарантирует wildcard allowlist в `exec-approvals.json`."""
    payload = _read_json(approvals_path)
    if not payload:
        payload = _default_exec_approvals_payload(approvals_path)

    changed_agents: list[str] = []
    defaults = _ensure_dict(payload, "defaults")
    if defaults.get("security") != "allowlist":
        defaults["security"] = "allowlist"
        changed_agents.append("defaults.security")
    if defaults.get("autoAllowSkills") is not True:
        defaults["autoAllowSkills"] = True
        changed_agents.append("defaults.autoAllowSkills")

    agents = _ensure_dict(payload, "agents")
    for agent_id in agent_ids:
        agent_payload = agents.get(agent_id)
        if not isinstance(agent_payload, dict):
            agent_payload = {}
            agents[agent_id] = agent_payload
        if _ensure_wildcard_allowlist(agent_payload):
            changed_agents.append(agent_id)

    if changed_agents:
        _write_json(approvals_path, payload)

    return {
        "path": str(approvals_path),
        "changed": bool(changed_agents),
        "changes": changed_agents,
    }


def sync_god_mode(
    openclaw_path: Path,
    approvals_path: Path,
    *,
    agent_id: str = "main",
) -> dict[str, Any]:
    """Синхронизирует оба runtime-файла и возвращает краткий отчёт."""
    openclaw_report = sync_openclaw_json(openclaw_path, agent_id=agent_id)
    approvals_report = sync_exec_approvals(approvals_path, agent_ids=(agent_id, "*"))
    return {
        "ok": True,
        "openclaw": openclaw_report,
        "approvals": approvals_report,
        "changed": bool(openclaw_report["changed"] or approvals_report["changed"]),
    }


def _build_parser() -> argparse.ArgumentParser:
    """Создаёт CLI parser."""
    parser = argparse.ArgumentParser(description="Синхронизирует persistent God Mode для OpenClaw.")
    parser.add_argument(
        "--openclaw-path",
        default=str(Path.home() / ".openclaw" / "openclaw.json"),
        help="Путь к runtime openclaw.json",
    )
    parser.add_argument(
        "--approvals-path",
        default=str(Path.home() / ".openclaw" / "exec-approvals.json"),
        help="Путь к exec approvals store",
    )
    parser.add_argument(
        "--agent-id",
        default="main",
        help="ID агента, для которого нужно закрепить full profile и wildcard allowlist",
    )
    return parser


def main() -> int:
    """CLI entrypoint."""
    parser = _build_parser()
    args = parser.parse_args()

    report = sync_god_mode(
        Path(args.openclaw_path).expanduser(),
        Path(args.approvals_path).expanduser(),
        agent_id=str(args.agent_id or "main").strip() or "main",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
