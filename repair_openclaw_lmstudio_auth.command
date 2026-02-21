#!/bin/zsh
# ------------------------------------------------------------------
# Восстановление auth профиля lmstudio для OpenClaw.
# Проверяет/чинит ~/.openclaw/agents/main/agent/auth-profiles.json
# ------------------------------------------------------------------

set -euo pipefail

AUTH_PATH="${OPENCLAW_AUTH_PROFILES_PATH:-$HOME/.openclaw/agents/main/agent/auth-profiles.json}"
LMSTUDIO_API_KEY="${LM_STUDIO_API_KEY:-${LMSTUDIO_API_KEY:-lm-studio}}"

/usr/bin/env python3 - "$AUTH_PATH" "$LMSTUDIO_API_KEY" <<'PY'
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

auth_path = Path(sys.argv[1]).expanduser()
api_key = str(sys.argv[2]).strip() or "lm-studio"


def contains_lmstudio(payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_lower = str(key).strip().lower()
            if key_lower == "lmstudio":
                return True
            if key_lower in {"provider", "provider_id", "name", "id"} and str(value).strip().lower() == "lmstudio":
                return True
            if contains_lmstudio(value):
                return True
        return False
    if isinstance(payload, list):
        return any(contains_lmstudio(item) for item in payload)
    if isinstance(payload, str):
        return payload.strip().lower() == "lmstudio"
    return False


def patch_payload(payload: Any) -> Any:
    entry = {"provider": "lmstudio", "apiKey": api_key}

    if payload is None:
        return {"providers": {"lmstudio": {"apiKey": api_key}}}

    if isinstance(payload, list):
        payload.append(entry)
        return payload

    if isinstance(payload, dict):
        if isinstance(payload.get("profiles"), list):
            payload["profiles"].append(entry)
            return payload
        if isinstance(payload.get("providers"), dict):
            providers = payload["providers"]
            if "lmstudio" not in providers:
                providers["lmstudio"] = {"apiKey": api_key}
            return payload
        if "lmstudio" not in payload:
            payload["lmstudio"] = {"apiKey": api_key}
        return payload

    return {"providers": {"lmstudio": {"apiKey": api_key}}}


payload: Any = None
if auth_path.exists():
    try:
        payload = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARN] Не удалось прочитать JSON, будет создан новый файл: {exc}")
        payload = None

if contains_lmstudio(payload):
    print(f"[OK] lmstudio профиль уже присутствует: {auth_path}")
    sys.exit(0)

patched = patch_payload(payload)
auth_path.parent.mkdir(parents=True, exist_ok=True)

if auth_path.exists():
    backup = auth_path.with_suffix(auth_path.suffix + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    backup.write_text(auth_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"[INFO] Создан backup: {backup}")

auth_path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"[OK] Профиль lmstudio добавлен: {auth_path}")
print("[INFO] Если OpenClaw уже запущен — перезапусти gateway.")
PY
