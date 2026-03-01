# -*- coding: utf-8 -*-
"""
Утилиты runtime-перезагрузки секретов OpenClaw.

Зачем нужен:
- После переключения tier-ключа в models.json требуется применить изменения
  в живом gateway без ручного рестарта.

Связи:
- Вызывается из OpenClawClient при failover и из web write-endpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any


async def reload_openclaw_secrets(timeout_sec: float = 25.0) -> dict[str, Any]:
    """Выполняет `openclaw secrets reload` и возвращает нормализованный результат."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "openclaw",
            "secrets",
            "reload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        output = stdout.decode("utf-8", errors="replace").strip()
        return {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode or 0),
            "output": output[-2000:],
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "exit_code": 124,
            "output": "secrets_reload_timeout",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "exit_code": 1,
            "output": f"secrets_reload_error:{exc}",
        }
