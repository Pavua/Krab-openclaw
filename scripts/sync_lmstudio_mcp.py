#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Синхронизация `~/.lmstudio/mcp.json` с curated MCP-реестром проекта.

Зачем:
- LM Studio и Krab/OpenClaw должны видеть один и тот же базовый набор MCP-инструментов;
- GUI-приложение LM Studio плохо дружит с `.env`, поэтому конфиг строится через
  managed launcher-скрипт проекта;
- optional серверы с отсутствующими ключами не должны засорять runtime ложными
  "broken" записями.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.mcp_registry import LMSTUDIO_MCP_PATH, build_lmstudio_mcp_json


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы CLI."""
    parser = argparse.ArgumentParser(description="Собирает и синхронизирует LM Studio mcp.json.")
    parser.add_argument(
        "--path",
        default=str(LMSTUDIO_MCP_PATH),
        help="Путь до LM Studio mcp.json.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Записать итоговый JSON на диск.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="Перед записью сохранить timestamp backup текущего файла.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Только проверить, совпадает ли текущий файл с ожидаемым конфигом.",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        help="Исключить high-risk серверы вроде shell/filesystem-home.",
    )
    parser.add_argument(
        "--include-optional-missing",
        action="store_true",
        help="Включить optional серверы даже без обязательных API-ключей.",
    )
    parser.add_argument(
        "--no-merge-existing",
        action="store_true",
        help="Не сохранять посторонние existing-серверы из текущего файла.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    """Читает JSON-файл, если он существует."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_existing_servers(
    existing: dict[str, Any],
    managed: dict[str, Any],
    managed_names: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """Обновляет managed-сервера, сохраняя посторонние custom записи."""
    existing_servers = dict(existing.get("mcpServers", {}) or {})
    managed_name_set = set(managed_names)
    preserved = sorted(name for name in existing_servers if name not in managed_name_set)
    existing_servers = {
        name: payload
        for name, payload in existing_servers.items()
        if name not in managed_name_set
    }
    existing_servers.update(managed)
    return {
        "mcpServers": existing_servers,
    }, preserved


def _write_backup(path: Path) -> Path:
    """Создаёт timestamp backup текущего LM Studio mcp.json."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_suffix(f".backup_{timestamp}.json")
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup_path


def main() -> int:
    """Точка входа CLI."""
    args = parse_args()
    target_path = Path(args.path).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    managed_payload, summary = build_lmstudio_mcp_json(
        include_optional_missing=args.include_optional_missing,
        include_high_risk=not args.safe,
    )
    existing_payload = _read_json(target_path)

    preserved: list[str] = []
    if args.no_merge_existing:
        final_payload = managed_payload
    else:
        final_payload, preserved = _merge_existing_servers(
            existing_payload,
            managed_payload["mcpServers"],
            summary["managed_names"],
        )

    rendered = json.dumps(final_payload, ensure_ascii=False, indent=2) + "\n"
    current = target_path.read_text(encoding="utf-8") if target_path.exists() else ""

    print(f"LM Studio mcp path: {target_path}")
    print(f"Managed included: {', '.join(summary['included']) or '—'}")
    print(f"Skipped missing env: {', '.join(summary['skipped_missing']) or '—'}")
    print(f"Skipped risk: {', '.join(summary['skipped_risk']) or '—'}")
    print(f"Preserved existing custom: {', '.join(preserved) or '—'}")

    if args.check:
        if current == rendered:
            print("OK: mcp.json уже синхронизирован.")
            return 0
        print("DIFF: mcp.json требует синхронизации.")
        return 1

    if not args.write:
        print(rendered)
        return 0

    backup_path: Path | None = None
    if args.backup and target_path.exists() and current != rendered:
        backup_path = _write_backup(target_path)

    target_path.write_text(rendered, encoding="utf-8")
    print("OK: mcp.json обновлён.")
    if backup_path:
        print(f"Backup: {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
