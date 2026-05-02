#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
User-friendly setup helper для LM Studio Bearer token.

Зачем:
- LM Studio с включённым `Require Authentication` отвечает 401 без bearer header;
- ручное редактирование `.env` неудобно и подвержено ошибкам (кавычки, whitespace);
- скрипт валидирует токен, пробует достучаться до LM Studio, и аккуратно
  обновляет `LM_API_TOKEN=...` в `.env` (без затирания остального).

Usage:
    venv/bin/python scripts/setup_lm_studio_token.py <token>
    venv/bin/python scripts/setup_lm_studio_token.py --check

Session 33: Wave 8-C — фиксит `!uptime` LM Studio Status 401.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Tuple

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_LM_URL = "http://192.168.0.171:1234"
ENV_KEY = "LM_API_TOKEN"
PROBE_TIMEOUT = 5.0


def validate_token(token: str) -> Tuple[bool, str]:
    """Базовая валидация токена. Returns (ok, error_message)."""
    if not token:
        return False, "токен пустой"
    if token != token.strip():
        return False, "токен содержит leading/trailing whitespace"
    if any(c.isspace() for c in token):
        return False, "токен содержит whitespace внутри (пробелы/табы/переносы)"
    if len(token) < 4:
        return False, "токен слишком короткий (<4 символов)"
    return True, ""


def read_env_url(env_path: Path = ENV_PATH) -> str:
    """Читает LM_STUDIO_URL из .env или возвращает default."""
    if not env_path.exists():
        return DEFAULT_LM_URL
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("LM_STUDIO_URL="):
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value.rstrip("/")
    except Exception:
        pass
    return DEFAULT_LM_URL


def read_existing_token(env_path: Path = ENV_PATH) -> str:
    """Возвращает текущий LM_API_TOKEN из .env (или пустую строку)."""
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{ENV_KEY}="):
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                return value
    except Exception:
        pass
    return ""


def probe_lm_studio(
    base_url: str,
    token: str,
    timeout: float = PROBE_TIMEOUT,
) -> Tuple[bool, str]:
    """
    Пробует GET /v1/models с Bearer header.
    Returns (ok, message).
    """
    url = f"{base_url.rstrip('/')}/v1/models"
    headers = {
        "Authorization": f"Bearer {token}",
        "x-api-key": token,
        "Accept": "application/json",
    }
    try:
        resp = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.ConnectError as exc:
        return False, f"connection error: {exc}. Hint: проверь что LM Studio запущена и Network доступен."
    except httpx.TimeoutException:
        return False, f"timeout {timeout}s. Hint: LM Studio не отвечает по {base_url}."
    except Exception as exc:
        return False, f"unexpected error: {exc}"

    if resp.status_code == 200:
        try:
            data = resp.json()
            count = len(data.get("data") or [])
            return True, f"OK 200, {count} models available"
        except Exception:
            return True, "OK 200 (response not JSON, but auth passed)"
    if resp.status_code == 401:
        return False, (
            f"401 Unauthorized — токен не принят LM Studio. "
            f"Hint: открой LM Studio → Settings → Network → API token и скопируй текущий."
        )
    if resp.status_code == 404:
        return False, (
            f"404 Not Found — endpoint `/v1/models` не найден. "
            f"Hint: проверь LM_STUDIO_URL ({base_url}) — может быть неверный порт."
        )
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


def upsert_env_token(token: str, env_path: Path = ENV_PATH) -> bool:
    """
    Обновляет или добавляет `LM_API_TOKEN=<token>` в `.env`.
    Returns True если строка была заменена, False если добавлена.
    Создаёт `.env` если он не существует.
    """
    line_to_write = f'{ENV_KEY}="{token}"'
    if not env_path.exists():
        env_path.write_text(line_to_write + "\n", encoding="utf-8")
        return False

    lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    new_lines: list[str] = []
    for line in lines:
        if line.strip().startswith(f"{ENV_KEY}=") and not replaced:
            new_lines.append(line_to_write)
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(line_to_write)

    # Сохраняем trailing newline для cleanliness
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return replaced


def cmd_check(env_path: Path = ENV_PATH) -> int:
    """Проверяет существующий токен без модификации .env."""
    token = read_existing_token(env_path)
    base_url = read_env_url(env_path)
    if not token:
        print(f"[check] {ENV_KEY} не найден в {env_path}.")
        print(f"        Запусти: venv/bin/python scripts/setup_lm_studio_token.py <token>")
        return 1

    ok_validate, err = validate_token(token)
    if not ok_validate:
        print(f"[check] токен невалидный: {err}")
        return 1

    print(f"[check] token: ***{token[-4:]} (len={len(token)})")
    print(f"[check] probing {base_url}/v1/models ...")
    ok_probe, msg = probe_lm_studio(base_url, token)
    print(f"[check] {'OK' if ok_probe else 'FAIL'}: {msg}")
    return 0 if ok_probe else 1


def cmd_setup(token: str, env_path: Path = ENV_PATH, skip_probe: bool = False) -> int:
    """Валидирует, пробует, и сохраняет токен в .env."""
    ok_validate, err = validate_token(token)
    if not ok_validate:
        print(f"[setup] ОШИБКА: токен невалидный — {err}")
        return 2

    base_url = read_env_url(env_path)

    if not skip_probe:
        print(f"[setup] probing {base_url}/v1/models с новым токеном ...")
        ok_probe, msg = probe_lm_studio(base_url, token)
        if not ok_probe:
            print(f"[setup] ОШИБКА probe: {msg}")
            print(f"[setup] .env НЕ изменён. Проверь токен и повтори.")
            return 3
        print(f"[setup] probe OK: {msg}")

    replaced = upsert_env_token(token, env_path)
    action = "обновлён" if replaced else "добавлен"
    print(f"[setup] {ENV_KEY} {action} в {env_path}")
    print("[setup] Дальше: restart Krab чтобы apply.")
    print("        /Users/pablito/Antigravity_AGENTS/new\\ Stop\\ Krab.command")
    print("        /Users/pablito/Antigravity_AGENTS/new\\ start_krab.command")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Setup helper для LM Studio Bearer token (LM_API_TOKEN в .env).",
    )
    parser.add_argument(
        "token",
        nargs="?",
        help="LM Studio API token (получить в LM Studio → Settings → Network → API token)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only: проверить текущий токен без изменения .env",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Skip live probe (debugging only — НЕ рекомендуется)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check:
        return cmd_check()

    if not args.token:
        parser.print_help()
        print("\nПример:")
        print("  venv/bin/python scripts/setup_lm_studio_token.py sk-lm-XXXX:YYYY")
        print("  venv/bin/python scripts/setup_lm_studio_token.py --check")
        return 1

    return cmd_setup(args.token, skip_probe=args.skip_probe)


if __name__ == "__main__":
    sys.exit(main())
