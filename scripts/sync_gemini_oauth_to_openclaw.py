"""Wave 25-A: Sync gemini-cli OAuth tokens → OpenClaw auth-profiles.

Запускается launchd-агентом каждые 15 минут или при detection desync.

Source:      ~/.gemini/oauth_creds.json
Destination: ~/.openclaw/agents/main/agent/auth-profiles.json
             (key: 'google-gemini-cli:<email>')

Idempotent: если tokens уже совпадают — exit 0 без записи.
Atomic: пишет через temp file + os.replace для consistency.

OpenClaw хранит два набора полей в профиле:
  - access/refresh/expires — родные OpenClaw-поля
  - access_token/refresh_token/expiry_date — зеркало gemini-cli полей
Оба набора синхронизируются одновременно.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

GEMINI_CREDS = Path.home() / ".gemini/oauth_creds.json"
OPENCLAW_AUTH = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
LOG_FILE = Path.home() / ".openclaw/krab_runtime_state/oauth_resync.log"

# expiry_date из gemini-cli — миллисекунды, expires в OpenClaw — тоже ms
_MS_PER_SEC = 1000


def log(msg: str) -> None:
    """Пишет сообщение в лог-файл и stdout."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with LOG_FILE.open("a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(msg)


def _expiry_in_min(expiry_date_ms: int | float) -> float:
    """Возвращает сколько минут до истечения токена (может быть отрицательным)."""
    return (expiry_date_ms / _MS_PER_SEC - time.time()) / 60


def main() -> int:
    # --- проверка source ---
    if not GEMINI_CREDS.exists():
        log("source missing — gemini-cli not logged in yet")
        return 0

    # --- проверка destination ---
    if not OPENCLAW_AUTH.exists():
        log("destination missing — openclaw auth-profiles not initialized")
        return 0

    # --- читаем источник ---
    try:
        g: dict = json.loads(GEMINI_CREDS.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"error reading gemini creds: {exc}")
        return 1

    # --- читаем destination ---
    try:
        ap: dict = json.loads(OPENCLAW_AUTH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log(f"error reading openclaw auth-profiles: {exc}")
        return 1

    # --- ищем профиль google-gemini-cli ---
    profs: dict = ap.get("profiles", {})
    if not isinstance(profs, dict):
        log("unexpected auth-profiles format — profiles is not a dict")
        return 1

    gcli_key: str | None = None
    for k in profs:
        if "google-gemini-cli" in k:
            gcli_key = k
            break

    if gcli_key is None:
        log("no google-gemini-cli profile found in openclaw auth-profiles")
        return 0

    cur: dict = profs[gcli_key]

    # --- проверяем нужен ли update (idempotent) ---
    new_access = g.get("access_token")
    new_refresh = g.get("refresh_token")
    new_expiry = g.get("expiry_date")

    needs_update = (
        cur.get("access_token") != new_access
        or cur.get("refresh_token") != new_refresh
        or cur.get("expiry_date") != new_expiry
        # родные OpenClaw поля тоже сверяем
        or cur.get("access") != new_access
        or cur.get("refresh") != new_refresh
        or cur.get("expires") != new_expiry
    )

    if not needs_update:
        exp = new_expiry or 0
        log(f"already synced — no-op (expiry_in_min={_expiry_in_min(exp):.1f})")
        return 0

    # --- обновляем оба набора полей ---
    cur.update(
        {
            # gemini-cli-style поля (зеркало)
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expiry_date": new_expiry,
            "token_type": g.get("token_type", "Bearer"),
            "scope": g.get("scope"),
            "id_token": g.get("id_token"),
            # родные OpenClaw-поля
            "access": new_access,
            "refresh": new_refresh,
            "expires": new_expiry,
        }
    )
    profs[gcli_key] = cur

    # --- пересобираем финальный документ ---
    if "profiles" in ap:
        ap["profiles"] = profs
    else:
        ap = {"version": 1, "profiles": profs}

    # --- atomic write через tmp ---
    tmp = OPENCLAW_AUTH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(ap, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, OPENCLAW_AUTH)
    except Exception as exc:  # noqa: BLE001
        log(f"error writing openclaw auth-profiles: {exc}")
        tmp.unlink(missing_ok=True)
        return 1

    exp_val = new_expiry or 0
    log(f"synced — gcli_key={gcli_key} expiry_in_min={_expiry_in_min(exp_val):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
