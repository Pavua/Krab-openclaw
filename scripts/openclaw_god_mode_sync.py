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
import contextlib
import json
import secrets
import shutil
import subprocess
import tempfile
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


def _sanitize_approvals_payload_for_gateway(payload: dict[str, Any]) -> dict[str, Any]:
    """Очищает approval-store до схемы, которую принимает `approvals set --gateway`.

    На диске OpenClaw терпит более богатые записи allowlist, но upload API gateway
    валидирует JSON строже и режет, например, `source`. Поэтому для live apply
    формируем отдельный временный payload без лишних полей, не ломая локальный
    persistent store.
    """
    sanitized = json.loads(json.dumps(payload))
    agents = sanitized.get("agents")
    if not isinstance(agents, dict):
        return sanitized

    for agent_payload in agents.values():
        if not isinstance(agent_payload, dict):
            continue
        allowlist = agent_payload.get("allowlist")
        if not isinstance(allowlist, list):
            continue
        cleaned_entries: list[dict[str, Any]] = []
        for raw_entry in allowlist:
            normalized = _normalize_allowlist_entry(raw_entry)
            if normalized is None:
                continue
            cleaned_entry = {"pattern": normalized["pattern"]}
            entry_id = str(normalized.get("id") or "").strip()
            if entry_id:
                cleaned_entry["id"] = entry_id
            last_used_at = normalized.get("lastUsedAt")
            if isinstance(last_used_at, int):
                cleaned_entry["lastUsedAt"] = last_used_at
            cleaned_entries.append(cleaned_entry)
        agent_payload["allowlist"] = cleaned_entries

    return sanitized


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

    if defaults.get("ask") != "off":
        defaults["ask"] = "off"
        changed_agents.append("defaults.ask")

    agents = _ensure_dict(payload, "agents")
    for agent_id in agent_ids:
        agent_payload = agents.get(agent_id)
        if not isinstance(agent_payload, dict):
            agent_payload = {}
            agents[agent_id] = agent_payload
        if _ensure_wildcard_allowlist(agent_payload):
            changed_agents.append(agent_id)
        if agent_payload.get("ask") != "off":
            agent_payload["ask"] = "off"
            changed_agents.append(f"{agent_id}.ask")

    if changed_agents:
        _write_json(approvals_path, payload)

    return {
        "path": str(approvals_path),
        "changed": bool(changed_agents),
        "changes": changed_agents,
    }


def apply_exec_approvals_to_gateway(
    approvals_path: Path,
    *,
    openclaw_bin: str | None = None,
    timeout_ms: int = 10000,
) -> dict[str, Any]:
    """Применяет approval-store в live gateway, чтобы host сразу перечитал policy.

    Почему это нужно:
    - начиная с OpenClaw 2026.4.x одного обновления файла на диске недостаточно;
    - host approvals слой может продолжать жить со stale snapshot до ручного approve
      в dashboard или до отдельного reload;
    - штатный `openclaw approvals set --gateway` синхронизирует live состояние без
      ручного клика по `Always allow`.
    """
    resolved_bin = str(openclaw_bin or "").strip() or shutil.which("openclaw") or ""
    if not resolved_bin:
        return {
            "attempted": False,
            "applied": False,
            "reason": "openclaw_cli_not_found",
        }

    # Homebrew openclaw uses `#!/usr/bin/env node` shebang which may fail when node
    # is not in the restricted PATH of the launcher environment. Fall back to invoking
    # node + openclaw.mjs directly when the binary is a symlink into node_modules.
    node_bin = shutil.which("node") or "/opt/homebrew/bin/node"
    _resolved = Path(resolved_bin).resolve()
    # After symlink resolution, /opt/homebrew/bin/openclaw → .../openclaw/openclaw.mjs
    openclaw_mjs = _resolved if _resolved.suffix == ".mjs" else _resolved.parent / "openclaw.mjs"
    use_node_direct = (
        bool(node_bin)
        and Path(node_bin).exists()
        and openclaw_mjs.exists()
    )

    raw_payload = _read_json(approvals_path)
    upload_payload = _sanitize_approvals_payload_for_gateway(raw_payload)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as temp_file:
        temp_path = Path(temp_file.name)
        json.dump(upload_payload, temp_file, ensure_ascii=False, indent=2)
        temp_file.write("\n")

    openclaw_args = [
        "approvals",
        "set",
        "--gateway",
        "--file",
        str(temp_path),
        "--timeout",
        str(int(timeout_ms)),
        "--json",
    ]
    command = (
        [node_bin, str(openclaw_mjs)] + openclaw_args
        if use_node_direct
        else [resolved_bin] + openclaw_args
    )

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return {
            "attempted": True,
            "applied": False,
            "reason": "spawn_failed",
            "error": str(exc),
        }
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()

    stdout = str(result.stdout or "").strip()
    stderr = str(result.stderr or "").strip()
    payload: dict[str, Any] | None = None
    if stdout:
        try:
            raw_payload = json.loads(stdout)
        except ValueError:
            raw_payload = None
        if isinstance(raw_payload, dict):
            payload = raw_payload

    applied = result.returncode == 0
    return {
        "attempted": True,
        "applied": applied,
        "returncode": result.returncode,
        "command": command,
        "stdout": stdout,
        "stderr": stderr,
        "payload": payload,
    }


def sync_god_mode(
    openclaw_path: Path,
    approvals_path: Path,
    *,
    agent_id: str = "main",
    apply_gateway: bool = True,
    openclaw_bin: str | None = None,
) -> dict[str, Any]:
    """Синхронизирует оба runtime-файла и возвращает краткий отчёт."""
    openclaw_report = sync_openclaw_json(openclaw_path, agent_id=agent_id)
    approvals_report = sync_exec_approvals(approvals_path, agent_ids=(agent_id, "*"))
    gateway_report = {
        "attempted": False,
        "applied": False,
        "reason": "disabled",
    }
    if apply_gateway:
        gateway_report = apply_exec_approvals_to_gateway(
            approvals_path,
            openclaw_bin=openclaw_bin,
        )
    return {
        "ok": True,
        "openclaw": openclaw_report,
        "approvals": approvals_report,
        "gateway_apply": gateway_report,
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
    parser.add_argument(
        "--skip-gateway-apply",
        action="store_true",
        help="Не отправлять approval-store в live gateway после записи файла",
    )
    parser.add_argument(
        "--openclaw-bin",
        default="",
        help="Явный путь к CLI openclaw для live apply approval-store",
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
        apply_gateway=not bool(args.skip_gateway_apply),
        openclaw_bin=str(args.openclaw_bin or "").strip() or None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
