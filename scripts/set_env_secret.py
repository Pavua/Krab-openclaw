#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Безопасная запись секретов в `.env` проекта.

Зачем:
- не заставлять пользователя вставлять токены в историю чата или shell;
- дать `.command`-обёрткам единый способ скрытого ввода секрета;
- канонизировать ключи окружения без ручного редактирования `.env`.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _quote_env_value(value: str) -> str:
    """Безопасно экранирует значение для dotenv-формата."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _canonical_key(raw_key: str) -> str:
    """Нормализует legacy-алиасы секретов к каноничным именам."""
    key = str(raw_key or "").strip().upper()
    if key == "LM_STUDIO_AUTH_TOKEN":
        return "LM_STUDIO_API_KEY"
    return key


def _upsert_env_key(path: Path, key: str, value: str) -> None:
    """Обновляет существующий ключ в `.env` или добавляет его в конец."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    target = f"{key}="
    updated = False
    out: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(target):
            indent = line[: len(line) - len(stripped)]
            out.append(f"{indent}{key}={_quote_env_value(value)}")
            updated = True
            continue
        if key == "LM_STUDIO_API_KEY" and stripped.startswith("LM_STUDIO_AUTH_TOKEN="):
            indent = line[: len(line) - len(stripped)]
            out.append(f"{indent}{key}={_quote_env_value(value)}")
            updated = True
            continue
        out.append(line)

    if not updated:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={_quote_env_value(value)}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    """Разбирает CLI-аргументы setter-скрипта."""
    parser = argparse.ArgumentParser(description="Скрыто записывает секрет в .env проекта.")
    parser.add_argument("env_key", help="Имя переменной окружения.")
    parser.add_argument(
        "--label",
        default="секрет",
        help="Человекочитаемое имя секрета для prompt.",
    )
    parser.add_argument(
        "--value",
        default="",
        help="Явное значение. Обычно не нужно: безопаснее оставить интерактивный ввод.",
    )
    parser.add_argument(
        "--env-path",
        default=str(ENV_PATH),
        help="Путь до целевого .env файла.",
    )
    return parser.parse_args()


def main() -> int:
    """Точка входа setter-скрипта."""
    args = parse_args()
    env_path = Path(args.env_path).expanduser()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    canonical_key = _canonical_key(args.env_key)

    value = str(args.value or "")
    if not value:
        first = getpass.getpass(f"Введите {args.label}: ").strip()
        if not first:
            print("❌ Пустое значение не сохранено.")
            return 2
        second = getpass.getpass(f"Повторите {args.label}: ").strip()
        if first != second:
            print("❌ Значения не совпали. Ничего не сохранено.")
            return 3
        value = first

    _upsert_env_key(env_path, canonical_key, value)
    print(f"✅ Секрет сохранён: {canonical_key} -> {env_path}")
    print("ℹ️ Если LM Studio / Codex уже открыты, перезапустите их, чтобы новые env-переменные подхватились.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
