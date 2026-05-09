"""Wave 44-R-script-tools — общая инфраструктура для агентских bash-скриптов.

Используется codex-cli (gpt-5.5) внутри Krab agent loop, чтобы выполнять
реальные действия через bash-вызовы (Telegram MCP отключены после
incident 02.05 из-за hallucinated tool calls).

Все скрипты:
- Возвращают JSON с {"ok": bool, ...}.
- Логируют каждый запуск в /tmp/krab_agent_tools.log.
- Используют общую сессию kraab.session (read-only friendly) для Telegram.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO_ROOT / "data" / "sessions"
SESSION_NAME = "kraab"  # main userbot session
LOG_PATH = Path("/tmp/krab_agent_tools.log")

# Whitelisted chat ids: Krab Swarm group + owner DM.
KRAB_SWARM_GROUP_ID = -1003703978531
OWNER_DM_ID = 312322764
DEFAULT_ALLOWED_CHAT_IDS = {KRAB_SWARM_GROUP_ID, OWNER_DM_ID}


def _load_env() -> dict[str, str]:
    """Грузим .env примитивно (без python-dotenv)."""
    env_path = REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if not env_path.is_file():
        return out
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def get_telegram_credentials() -> tuple[int, str]:
    """Возвращает (api_id, api_hash). Падает с понятной ошибкой."""
    env = {**os.environ, **_load_env()}
    api_id_raw = env.get("TELEGRAM_API_ID", "")
    api_hash = env.get("TELEGRAM_API_HASH", "")
    if not api_id_raw or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID/TELEGRAM_API_HASH not found in env or .env")
    return int(api_id_raw), api_hash


def session_path() -> Path:
    return SESSION_DIR / f"{SESSION_NAME}.session"


def log_invocation(script: str, args: list[str], result: dict[str, Any]) -> None:
    """Append-only audit log."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        ok = bool(result.get("ok"))
        line = json.dumps(
            {
                "ts": ts,
                "script": script,
                "args": args,
                "ok": ok,
                "result": result,
            },
            ensure_ascii=False,
        )
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def emit_json(payload: dict[str, Any], script: str, args: list[str]) -> None:
    """Печатает JSON на stdout + логирует. Никогда не бросает исключение."""
    log_invocation(script, args, payload)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def emit_error(error: str, script: str, args: list[str], hint: str = "") -> int:
    payload: dict[str, Any] = {"ok": False, "error": error}
    if hint:
        payload["hint"] = hint
    emit_json(payload, script, args)
    return 1
