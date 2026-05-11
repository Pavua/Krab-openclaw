"""Wave 44-T-multi-channel — общие helpers для Discord/iMessage/Email скриптов.

Обеспечивает:
- First-time-recipient guard (persisted JSON в ~/.openclaw/krab_runtime_state/)
- HARD-blocked recipients list (банки, юристы)
- Owner confirm token bypass
- Audit log в ~/.openclaw/krab_runtime_state/agent_audit.jsonl
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

# Wave 65-F: env override для isolation тестов (subprocess не наследует
# monkeypatch parent процесса, нужен env-var). Совпадает с pattern в
# src/userbot_bridge.py:1638 (KRAB_RUNTIME_STATE_DIR).
RUNTIME_STATE_DIR = Path(
    os.environ.get("KRAB_RUNTIME_STATE_DIR")
    or str(Path.home() / ".openclaw" / "krab_runtime_state")
).expanduser()
OWNER_CONFIRM_TOKEN_PATH = RUNTIME_STATE_DIR / "owner_confirm.token"
AGENT_AUDIT_PATH = RUNTIME_STATE_DIR / "agent_audit.jsonl"

DISCORD_KNOWN_PATH = RUNTIME_STATE_DIR / "discord_known_channels.json"
IMESSAGE_KNOWN_PATH = RUNTIME_STATE_DIR / "imessage_known.json"
EMAIL_KNOWN_PATH = RUNTIME_STATE_DIR / "email_known.json"

# HARD-blocked recipients — patterns that NEVER allowed regardless of confirm.
# Banks + lawyers + tax authorities. Conservative.
HARD_BLOCKED_PATTERNS = [
    re.compile(r"@.*(bank|sberbank|tinkoff|santander|bbva|caixa|chase|citibank)", re.I),
    re.compile(r"@.*(law|legal|abogad|lawyer|attorney|notar)", re.I),
    re.compile(r"@.*(hacienda|aeat|irs[-.]|treasury)", re.I),
    re.compile(r"@.*(police|policia|fbi|interpol)", re.I),
]


def is_hard_blocked(recipient: str) -> tuple[bool, str]:
    """Returns (blocked, reason). Recipient может быть email, phone, или channel name."""
    s = recipient.lower()
    for pat in HARD_BLOCKED_PATTERNS:
        if pat.search(s):
            return True, f"recipient matches hard-blocked pattern: {pat.pattern}"
    return False, ""


def _load_known(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_known(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_known_recipient(path: Path, key: str) -> bool:
    data = _load_known(path)
    return key in data


def remember_recipient(path: Path, key: str, meta: dict[str, Any] | None = None) -> None:
    data = _load_known(path)
    data[key] = {
        "first_seen": data.get(key, {}).get("first_seen", time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        "last_used": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        **(meta or {}),
    }
    _save_known(path, data)


def check_owner_token(provided: str | None) -> bool:
    """Если provided совпадает с содержимым owner_confirm.token — bypass first-time."""
    if not provided:
        return False
    if not OWNER_CONFIRM_TOKEN_PATH.is_file():
        return False
    try:
        actual = OWNER_CONFIRM_TOKEN_PATH.read_text(encoding="utf-8").strip()
        return bool(actual) and actual == provided.strip()
    except OSError:
        return False


def first_time_gate(
    known_path: Path,
    recipient_key: str,
    first_time_confirm: bool,
    owner_token: str | None,
) -> tuple[bool, str]:
    """Returns (allowed, reason).

    - Если recipient уже известен — allowed.
    - Если first_time_confirm флаг есть — allowed.
    - Если owner_token валиден — allowed.
    - Иначе — blocked.
    """
    if is_known_recipient(known_path, recipient_key):
        return True, "known_recipient"
    if first_time_confirm:
        return True, "first_time_confirmed"
    if check_owner_token(owner_token):
        return True, "owner_token_bypass"
    return False, "first_time_no_confirm"


def audit_event(channel: str, recipient: str, action: str, ok: bool, extra: dict[str, Any]) -> None:
    """Append-only audit log в ~/.openclaw/krab_runtime_state/agent_audit.jsonl."""
    try:
        AGENT_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "channel": channel,
            "recipient": recipient,
            "action": action,
            "ok": ok,
            **extra,
        }
        with AGENT_AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def discord_webhook_url() -> str | None:
    """Discord webhook URL из env (KRAB_DISCORD_WEBHOOK_<SERVER>_<CHANNEL> или DEFAULT)."""
    return os.environ.get("KRAB_DISCORD_WEBHOOK_URL") or None


def discord_webhook_for(server: str, channel: str) -> str | None:
    """Resolve webhook для конкретной server/channel пары."""
    key = (
        f"KRAB_DISCORD_WEBHOOK_{server.upper().replace('-', '_').replace(' ', '_')}_"
        f"{channel.upper().replace('-', '_').replace('#', '').replace(' ', '_')}"
    )
    val = os.environ.get(key)
    if val:
        return val
    return discord_webhook_url()
