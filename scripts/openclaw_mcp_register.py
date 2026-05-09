#!/usr/bin/env python3
"""OpenClaw MCP register helper.

Idempotent CLI для регистрации MCP-серверов в OpenClaw из реестра
``scripts/mcp_inventory.toml``.

Usage:
    python scripts/openclaw_mcp_register.py --list
    python scripts/openclaw_mcp_register.py --add github
    python scripts/openclaw_mcp_register.py --add-all-with-tokens
    python scripts/openclaw_mcp_register.py --remove github
    python scripts/openclaw_mcp_register.py --add github --dry-run

Дизайн: subprocess к ``openclaw mcp set/unset/list``. Если `openclaw` бинарь
недоступен, --dry-run всё равно работает (печатает план).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:  # Python 3.11+ — stdlib tomllib
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_INVENTORY = Path(__file__).resolve().parent / "mcp_inventory.toml"
ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class RegisterError(RuntimeError):
    """Ошибки команд openclaw / валидации."""


def load_inventory(path: Path) -> dict[str, dict[str, Any]]:
    """Парсит TOML-реестр и возвращает dict {name: spec}."""
    if not path.exists():
        raise RegisterError(f"Inventory not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise RegisterError("Inventory root must be a table")
    return {name: spec for name, spec in data.items() if isinstance(spec, dict)}


def resolve_env_placeholders(
    value: Any,
    env: dict[str, str],
    *,
    keep_placeholder: bool = True,
) -> Any:
    """Резолвит ``${VAR}`` подстановки рекурсивно.

    Если ``keep_placeholder=True`` — отсутствующие vars остаются как ``${VAR}``
    (OpenClaw сам резолвит через shellEnv). Если False — поднимает RegisterError.
    """
    if isinstance(value, str):
        def _sub(match: re.Match[str]) -> str:
            name = match.group(1)
            if name in env and env[name]:
                return env[name]
            if keep_placeholder:
                return match.group(0)
            raise RegisterError(f"Missing env var: {name}")

        return ENV_VAR_PATTERN.sub(_sub, value)
    if isinstance(value, list):
        return [resolve_env_placeholders(v, env, keep_placeholder=keep_placeholder) for v in value]
    if isinstance(value, dict):
        return {
            k: resolve_env_placeholders(v, env, keep_placeholder=keep_placeholder)
            for k, v in value.items()
        }
    return value


def has_required_tokens(spec: dict[str, Any], env: dict[str, str]) -> tuple[bool, list[str]]:
    """Проверяет наличие всех required_env. Возвращает (ok, missing)."""
    required: list[str] = list(spec.get("required_env", []) or [])
    missing = [v for v in required if not env.get(v)]
    return (not missing, missing)


def build_openclaw_payload(spec: dict[str, Any]) -> dict[str, Any]:
    """Преобразует TOML-spec в JSON для ``openclaw mcp set``.

    Шаблоны ``${VAR}`` ОСТАВЛЯЕМ в payload — OpenClaw резолвит через shellEnv.
    Поля ``description`` / ``required_env`` / ``notes`` — meta, не идут в payload.
    """
    payload: dict[str, Any] = {}
    transport = spec.get("transport")
    # OpenClaw принимает только "sse" / "streamable-http" / "stdio".
    # "http" — алиас, маппим в "streamable-http" для удобства из canonical .mcp.json.
    if transport == "http":
        transport = "streamable-http"
    if transport in ("streamable-http", "sse"):
        payload["transport"] = transport
        url = spec.get("url")
        if not url:
            raise RegisterError("http/sse spec requires 'url'")
        payload["url"] = url
        headers = spec.get("headers")
        if isinstance(headers, dict) and headers:
            payload["headers"] = dict(headers)
    elif transport == "stdio":
        command = spec.get("command")
        if not command:
            raise RegisterError("stdio spec requires 'command'")
        payload["command"] = command
        args = spec.get("args") or []
        if args:
            payload["args"] = list(args)
        env = spec.get("env")
        if isinstance(env, dict) and env:
            payload["env"] = dict(env)
    else:
        raise RegisterError(f"Unknown transport: {transport!r}")
    return payload


def list_registered(openclaw_bin: str = "openclaw") -> list[str]:
    """Парсит ``openclaw mcp list`` → список имён."""
    try:
        result = subprocess.run(
            [openclaw_bin, "mcp", "list"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RegisterError(f"openclaw mcp list failed: {exc}") from exc
    names: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("- "):
            names.append(line[2:].strip())
    return names


def register_one(
    name: str,
    payload: dict[str, Any],
    *,
    dry_run: bool = False,
    openclaw_bin: str = "openclaw",
) -> str:
    """Регистрирует один MCP. Возвращает строку для отчёта."""
    payload_json = json.dumps(payload, ensure_ascii=False)
    if dry_run:
        return f"[dry-run] openclaw mcp set {name} '{payload_json}'"
    try:
        subprocess.run(
            [openclaw_bin, "mcp", "set", name, payload_json],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr or ""
        raise RegisterError(f"openclaw mcp set {name} failed: {stderr.strip()}") from exc
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RegisterError(f"openclaw mcp set {name} failed: {exc}") from exc
    return f"registered {name}"


def remove_one(name: str, *, dry_run: bool = False, openclaw_bin: str = "openclaw") -> str:
    """Удаляет MCP."""
    if dry_run:
        return f"[dry-run] openclaw mcp unset {name}"
    try:
        subprocess.run(
            [openclaw_bin, "mcp", "unset", name],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise RegisterError(f"openclaw mcp unset {name} failed: {exc.stderr}") from exc
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RegisterError(f"openclaw mcp unset {name} failed: {exc}") from exc
    return f"removed {name}"


def cmd_list(inv: dict[str, dict[str, Any]], env: dict[str, str], *, openclaw_bin: str) -> int:
    """Печатает: registered (live) + available (inventory) с отметкой токенов."""
    try:
        registered = list_registered(openclaw_bin)
    except RegisterError as exc:
        print(f"WARN: cannot read live MCPs: {exc}", file=sys.stderr)
        registered = []

    print("=== Registered (live `openclaw mcp list`) ===")
    if registered:
        for name in registered:
            print(f"  - {name}")
    else:
        print("  (none or openclaw bin unavailable)")

    print("\n=== Inventory (scripts/mcp_inventory.toml) ===")
    for name, spec in sorted(inv.items()):
        ok, missing = has_required_tokens(spec, env)
        status = "OK" if ok else f"MISSING: {','.join(missing)}"
        is_reg = "[registered]" if name in registered else "[available]"
        desc = spec.get("description", "")
        print(f"  {is_reg:14} {name:14} {status:30} {desc}")
    return 0


def cmd_add(
    name: str,
    inv: dict[str, dict[str, Any]],
    env: dict[str, str],
    *,
    dry_run: bool,
    openclaw_bin: str,
) -> int:
    if name not in inv:
        print(f"ERROR: '{name}' not in inventory", file=sys.stderr)
        return 2
    spec = inv[name]
    ok, missing = has_required_tokens(spec, env)
    if not ok:
        print(f"ERROR: cannot register '{name}' — missing env: {missing}", file=sys.stderr)
        return 3
    payload = build_openclaw_payload(spec)
    msg = register_one(name, payload, dry_run=dry_run, openclaw_bin=openclaw_bin)
    print(msg)
    return 0


def cmd_remove(name: str, *, dry_run: bool, openclaw_bin: str) -> int:
    msg = remove_one(name, dry_run=dry_run, openclaw_bin=openclaw_bin)
    print(msg)
    return 0


def cmd_add_all_with_tokens(
    inv: dict[str, dict[str, Any]],
    env: dict[str, str],
    *,
    dry_run: bool,
    openclaw_bin: str,
) -> int:
    skipped: list[tuple[str, list[str]]] = []
    registered: list[str] = []
    failed: list[tuple[str, str]] = []
    try:
        already = set(list_registered(openclaw_bin))
    except RegisterError:
        already = set()
    for name, spec in sorted(inv.items()):
        ok, missing = has_required_tokens(spec, env)
        if not ok:
            skipped.append((name, missing))
            continue
        if name in already and not dry_run:
            # idempotency: уже есть → переустанавливаем (config мог измениться)
            pass
        try:
            payload = build_openclaw_payload(spec)
            register_one(name, payload, dry_run=dry_run, openclaw_bin=openclaw_bin)
            registered.append(name)
        except RegisterError as exc:
            failed.append((name, str(exc)))

    print(f"Registered ({len(registered)}): {', '.join(registered) or '(none)'}")
    if skipped:
        print("Skipped (missing tokens):")
        for name, missing in skipped:
            print(f"  - {name}: {','.join(missing)}")
    if failed:
        print("Failed:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenClaw MCP register helper")
    p.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY,
        help="Path to mcp_inventory.toml",
    )
    p.add_argument("--openclaw-bin", default="openclaw", help="openclaw CLI path")
    p.add_argument("--dry-run", action="store_true", help="Print plan without executing")
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--list", action="store_true", help="Show registered + available")
    grp.add_argument("--add", metavar="NAME", help="Register single MCP")
    grp.add_argument("--remove", metavar="NAME", help="Unregister single MCP")
    grp.add_argument(
        "--add-all-with-tokens",
        action="store_true",
        help="Register all MCPs whose required_env is populated",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inv = load_inventory(args.inventory)
    except RegisterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4

    env = dict(os.environ)
    bin_path = args.openclaw_bin
    if not args.dry_run and shutil.which(bin_path) is None:
        # warn — но не падаем для list (он сам обработает)
        print(f"WARN: '{bin_path}' not found in PATH", file=sys.stderr)

    if args.list:
        return cmd_list(inv, env, openclaw_bin=bin_path)
    if args.add:
        return cmd_add(args.add, inv, env, dry_run=args.dry_run, openclaw_bin=bin_path)
    if args.remove:
        return cmd_remove(args.remove, dry_run=args.dry_run, openclaw_bin=bin_path)
    if args.add_all_with_tokens:
        return cmd_add_all_with_tokens(
            inv, env, dry_run=args.dry_run, openclaw_bin=bin_path
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
