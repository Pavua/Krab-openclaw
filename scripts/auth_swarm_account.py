#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Интерактивная авторизация Telegram-аккаунтов для per-team свёрма.

Использование:
    python scripts/auth_swarm_account.py --team traders
    python scripts/auth_swarm_account.py --all

Читает конфиг из ~/.openclaw/krab_runtime_state/swarm_team_accounts.json.
Сессии сохраняются в data/sessions/ рядом с kraab.session.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Добавляем корень проекта в sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from pyrogram import Client  # noqa: E402

from src.config import config  # noqa: E402


def auth_team(team: str, acct: dict[str, str]) -> bool:
    """Авторизует один team-аккаунт."""
    session_name = acct.get("session_name", f"swarm_{team}")
    phone = acct.get("phone", "")
    workdir = str(_ROOT / "data" / "sessions")

    print(f"\n{'='*50}")
    print(f"🐝 Авторизация: {team} → сессия {session_name}")
    if phone:
        print(f"📱 Телефон: {phone}")
    print(f"📂 Каталог: {workdir}")
    print(f"{'='*50}")

    Path(workdir).mkdir(parents=True, exist_ok=True)

    kwargs: dict = {
        "api_id": config.TELEGRAM_API_ID,
        "api_hash": config.TELEGRAM_API_HASH,
        "workdir": workdir,
    }
    if phone:
        kwargs["phone_number"] = phone

    app = Client(session_name, **kwargs)

    try:
        app.start()
        me = app.get_me()
        app.stop()
        print(f"✅ {team}: @{me.username or 'unknown'} (id={me.id})")
        return True
    except Exception as exc:
        print(f"❌ {team}: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Авторизация swarm team accounts")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--team", help="Имя команды (traders, coders, analysts, creative)")
    group.add_argument("--all", action="store_true", help="Авторизовать все команды")
    args = parser.parse_args()

    accounts = config.load_swarm_team_accounts()
    if not accounts:
        print(f"❌ Конфиг не найден: {config.SWARM_TEAM_ACCOUNTS_PATH}")
        print("Создайте JSON файл с форматом:")
        print('  {"traders": {"session_name": "swarm_traders", "phone": "+34..."}, ...}')
        return 1

    if args.team:
        if args.team not in accounts:
            print(f"❌ Команда '{args.team}' не найдена в конфиге.")
            print(f"Доступные: {', '.join(accounts.keys())}")
            return 1
        ok = auth_team(args.team, accounts[args.team])
        return 0 if ok else 1

    # --all
    results: dict[str, bool] = {}
    for team, acct in accounts.items():
        results[team] = auth_team(team, acct)

    print(f"\n{'='*50}")
    print("📊 Итого:")
    for team, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {team}")

    failed = sum(1 for ok in results.values() if not ok)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
