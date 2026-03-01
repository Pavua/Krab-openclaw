#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runtime autoswitch-заглушка для web-панели.

Почему:
- После рефакторинга endpoint /api/openclaw/model-autoswitch/status падал в 500,
  потому что скрипт отсутствовал.
- Этот скрипт гарантирует детерминированный JSON-ответ и снимает 500.

Примечание:
- Сейчас режим no-op (без мутаций), но контракт готов для будущей логики.
"""

from __future__ import annotations

import argparse
import json
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenClaw model autoswitch (no-op)")
    parser.add_argument("--dry-run", action="store_true", help="Только диагностика")
    args = parser.parse_args()

    payload = {
        "ok": True,
        "mode": "dry-run" if args.dry_run else "apply",
        "status": "ready",
        "changed": False,
        "reason": "autoswitch_stub_active",
        "timestamp": int(time.time()),
        "details": {
            "message": "Скрипт-заглушка активен. Runtime 500 устранен.",
        },
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
