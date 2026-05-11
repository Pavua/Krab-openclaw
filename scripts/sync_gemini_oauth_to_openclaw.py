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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GEMINI_CREDS = Path.home() / ".gemini/oauth_creds.json"
OPENCLAW_AUTH = Path.home() / ".openclaw/agents/main/agent/auth-profiles.json"
LOG_FILE = Path.home() / ".openclaw/krab_runtime_state/oauth_resync.log"

# expiry_date из gemini-cli — миллисекунды, expires в OpenClaw — тоже ms
_MS_PER_SEC = 1000

# Wave 50-B: gemini-cli OAuth client (public — built into gemini-cli binary)
# Видно в любом id_token aud claim: <client_id>.apps.googleusercontent.com
_GEMINI_OAUTH_CLIENT_ID = (
    "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
)
# Google OAuth installed-app credentials. Для refresh-token grant Google требует
# и client_id и client_secret, но gemini-cli — installed application, секрет
# которого публично вшит в его bundle (он буквально хранится в строке вида
# OAUTH_CLIENT_SECRET = "..."). Не извлекаем явно в коде — читаем из gemini-cli
# bundle file или env override, чтобы избежать GitHub secret-scanning false-
# positive.
_GEMINI_OAUTH_CLIENT_SECRET_ENV = "KRAB_GEMINI_OAUTH_CLIENT_SECRET"
_GEMINI_CLI_BUNDLE_GLOB = "/opt/homebrew/Cellar/gemini-cli/*/libexec/lib/node_modules/@google/gemini-cli/bundle/chunk-*.js"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Force-refresh когда expiry прошёл больше чем 60 минут назад (защита от race)
_FORCE_REFRESH_THRESHOLD_MIN = -60.0


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


def _read_gemini_client_secret() -> str | None:
    """Достаёт OAuth client_secret из gemini-cli bundle или env override.

    Google OAuth refresh-token grant требует client_secret даже для installed
    apps. Для gemini-cli этот secret публично вшит в его JS bundle (видно
    `grep -r OAUTH_CLIENT_SECRET /opt/homebrew/Cellar/gemini-cli/`). Чтобы
    не хранить literal в репо (GitHub push protection срабатывает), читаем
    runtime из bundle или из env-override.
    """
    import glob
    import re

    env_override = os.environ.get(_GEMINI_OAUTH_CLIENT_SECRET_ENV)
    if env_override:
        return env_override

    for bundle_path in glob.glob(_GEMINI_CLI_BUNDLE_GLOB):
        try:
            with open(bundle_path, encoding="utf-8") as fh:
                content = fh.read()
        except OSError:
            continue
        match = re.search(r'OAUTH_CLIENT_SECRET\s*=\s*"([^"]+)"', content)
        if match:
            return match.group(1)
    return None


def _force_refresh_gemini_creds(creds: dict) -> dict | None:
    """Wave 50-B: refresh expired gemini-cli token via Google OAuth endpoint.

    gemini-cli не auto-refresh свой oauth_creds.json пока не запущен. Когда
    expiry_date в прошлом и Krab пользователю долго не нужен — daemon
    бесконечно лог-спамит 'already synced'. Этот helper берёт refresh_token
    и обменивает на свежий access_token через Google OAuth endpoint, затем
    возвращает обновлённый dict для записи обратно в oauth_creds.json.

    Returns None при любой ошибке (caller тогда продолжает с stale creds —
    sync всё равно nop, mirror up-to-date).
    """
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        log("force-refresh skipped: no refresh_token in gemini creds")
        return None

    client_secret = _read_gemini_client_secret()
    if not client_secret:
        log("force-refresh skipped: gemini-cli OAuth client_secret unavailable")
        return None

    payload = urllib.parse.urlencode(
        {
            "client_id": _GEMINI_OAUTH_CLIENT_ID,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")

    req = urllib.request.Request(  # noqa: S310 — Google OAuth endpoint
        _GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 400 invalid_grant — refresh_token revoked, нужен re-login
        err_body = ""
        try:
            err_body = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            pass
        log(f"force-refresh HTTP {exc.code}: {err_body[:200]}")
        return None
    except Exception as exc:  # noqa: BLE001
        log(f"force-refresh failed: {exc}")
        return None

    new_access = body.get("access_token")
    expires_in = int(body.get("expires_in", 3600))
    new_expiry_ms = int((time.time() + expires_in) * _MS_PER_SEC)

    if not new_access:
        log(f"force-refresh response missing access_token: keys={list(body.keys())}")
        return None

    # Merge: новые поля из response, остальное (refresh_token, scope) сохраняем
    updated = dict(creds)
    updated["access_token"] = new_access
    updated["expiry_date"] = new_expiry_ms
    if body.get("id_token"):
        updated["id_token"] = body["id_token"]
    if body.get("token_type"):
        updated["token_type"] = body["token_type"]
    # Note: Google НЕ обычно возвращает новый refresh_token при refresh —
    # сохраняем existing. Scope тоже sticks с original grant.

    log(f"force-refresh success: new expiry_in_min={_expiry_in_min(new_expiry_ms):.1f}")
    return updated


def _maybe_force_refresh(creds: dict) -> dict:
    """Wave 50-B: если token expired >60 min — force-refresh и записать обратно."""
    expiry = creds.get("expiry_date") or 0
    if _expiry_in_min(expiry) >= _FORCE_REFRESH_THRESHOLD_MIN:
        return creds

    log(f"token expired (expiry_in_min={_expiry_in_min(expiry):.1f}) — attempting force-refresh")
    refreshed = _force_refresh_gemini_creds(creds)
    if refreshed is None:
        return creds

    # Atomic write обратно в oauth_creds.json — gemini-cli при next invocation
    # подхватит. Permissions 0600 сохраняем.
    tmp = GEMINI_CREDS.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, GEMINI_CREDS)
    except Exception as exc:  # noqa: BLE001
        log(f"force-refresh: failed to write gemini creds: {exc}")
        tmp.unlink(missing_ok=True)
        return creds

    log("force-refresh: gemini creds updated on disk")
    return refreshed


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

    # Wave 50-B: если token expired больше threshold — force-refresh через
    # Google OAuth endpoint. Это разблокирует stale "already synced" loop
    # когда gemini-cli не запускался сам долго.
    g = _maybe_force_refresh(g)

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
