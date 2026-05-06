#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GCP Sales POC Email Watcher.

Поллит Gmail через IMAP, ищет письма от Google Cloud Support про Anthropic
quota / Sales POC. При нахождении — шлёт Telegram alert через Krab
``/api/notify`` endpoint.

Setup (один раз):
  1. Создай Gmail App Password: https://myaccount.google.com/apppasswords
  2. Добавь в ``.env``:
       EMAIL_USER=pavelr7@gmail.com
       EMAIL_APP_PASSWORD=<16-char app password>
  3. Опционально: ``EMAIL_USER_SECONDARY=pavelr7@rongfa.biz`` (если тоже Gmail)
  4. Скрипт зарегистрирован как LaunchAgent ai.krab.gcp-quota-poc-watcher,
     стартует каждые 30 мин

Логика:
- search для UNSEEN писем с from:cloudsupport@google.com OR sender contains
  google.com WITH subject contains anthropic|quota|sales|account team|case
- для каждого нового матча: POST /api/notify с превью + thread URL
- mark as SEEN чтобы не дублировать alerts
- state cache: ``~/.openclaw/krab_runtime_state/gcp_quota_poc_watcher.json``
  хранит set of processed message_ids (rolling 7 дней)
"""

from __future__ import annotations

import email
import imaplib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from email.header import decode_header
from pathlib import Path

# ─── Конфиг ─────────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
    """Простой .env loader для standalone запуска."""
    env_file = os.environ.get("ENV_FILE", str(Path(__file__).parent.parent / ".env"))
    p = Path(env_file)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

EMAIL_USER = os.environ.get("EMAIL_USER", "").strip()
EMAIL_APP_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "").strip()
IMAP_SERVER = os.environ.get("EMAIL_IMAP_SERVER", "imap.gmail.com").strip()
IMAP_PORT = int(os.environ.get("EMAIL_IMAP_PORT", "993"))

KRAB_NOTIFY_URL = os.environ.get("KRAB_NOTIFY_URL", "http://127.0.0.1:8080/api/notify")
NOTIFY_CHAT_ID = (
    os.environ.get("OWNER_NOTIFY_CHAT_ID") or os.environ.get("OPENCLAW_ALERT_TARGET") or "me"
)

STATE_FILE = Path.home() / ".openclaw" / "krab_runtime_state" / "gcp_quota_poc_watcher.json"

# Триггерные паттерны
SENDER_PATTERNS = (
    "cloudsupport@google.com",
    "@google.com",
    "@cloud.google.com",
)
SUBJECT_KEYWORDS = (
    "anthropic",
    "quota",
    "sales",
    "account team",
    "POC",
    "case",
    "support",
    "claude",
)


def _log(msg: str) -> None:
    """Простой stderr logger (LaunchAgent захватит)."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True, file=sys.stderr)


# ─── State management ───────────────────────────────────────────────────────


def _load_state() -> dict:
    """Читает persistent state с processed message_ids."""
    if not STATE_FILE.exists():
        return {"processed_ids": [], "last_run": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _log(f"state_read_failed error={exc}")
        return {"processed_ids": [], "last_run": None}


def _save_state(state: dict) -> None:
    """Atomic write через .tmp + rename."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        # Trim processed_ids к последним 200 entries (rolling 7 дней пред-я)
        if len(state.get("processed_ids", [])) > 200:
            state["processed_ids"] = state["processed_ids"][-200:]
        state["last_run"] = int(time.time())
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as exc:  # noqa: BLE001
        _log(f"state_write_failed error={exc}")


# ─── Trigger matcher ────────────────────────────────────────────────────────


def _decode(value: str | bytes) -> str:
    """Декодирует MIME-encoded headers (UTF-8 / quoted-printable / base64)."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            value = str(value)
    parts = decode_header(value)
    out = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                text = text.decode(charset or "utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                text = text.decode("utf-8", errors="replace")
        out.append(str(text))
    return "".join(out).strip()


def _matches_trigger(sender: str, subject: str) -> bool:
    """True если письмо соответствует Sales POC trigger pattern."""
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    # Sender check: должно быть от @google.com или @cloud.google.com
    sender_matches = any(p.lower() in sender_lower for p in SENDER_PATTERNS)
    if not sender_matches:
        return False

    # Subject check: достаточно одного keyword
    subject_matches = any(kw in subject_lower for kw in SUBJECT_KEYWORDS)
    return subject_matches


# ─── Telegram alert via Krab ────────────────────────────────────────────────


def _send_alert(sender: str, subject: str, body_preview: str) -> bool:
    """POST /api/notify с превью письма. Returns True при успехе."""
    text = (
        f"🔔 *GCP Sales POC email detected*\n\n"
        f"From: `{sender}`\n"
        f"Subject: {subject}\n\n"
        f"Preview:\n{body_preview[:400]}"
    )
    payload = json.dumps({"text": text, "chat_id": NOTIFY_CHAT_ID}).encode("utf-8")
    req = urllib.request.Request(
        KRAB_NOTIFY_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            _log(f"alert_sent ok={ok} status={resp.status}")
            return ok
    except urllib.error.HTTPError as exc:
        _log(f"alert_http_error status={exc.code} reason={exc.reason}")
        return False
    except Exception as exc:  # noqa: BLE001
        _log(f"alert_send_failed error={exc}")
        return False


# ─── Main poll ──────────────────────────────────────────────────────────────


def main() -> int:
    if not EMAIL_USER or not EMAIL_APP_PASSWORD:
        _log("config_missing — set EMAIL_USER + EMAIL_APP_PASSWORD in .env")
        return 78  # EX_CONFIG

    state = _load_state()
    processed = set(state.get("processed_ids", []))

    try:
        mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        mail.login(EMAIL_USER, EMAIL_APP_PASSWORD)
    except Exception as exc:  # noqa: BLE001
        _log(f"imap_login_failed error={exc}")
        return 1

    new_alerts = 0
    try:
        mail.select("INBOX")
        # Search для UNSEEN от @google.com за последние 7 дней
        status, data = mail.search(None, "UNSEEN", "FROM", "google.com")
        if status != "OK":
            _log(f"imap_search_failed status={status}")
            return 1

        msg_ids = data[0].split() if data and data[0] else []
        _log(f"poll_start unseen_from_google={len(msg_ids)} processed_count={len(processed)}")

        for msg_id_bytes in msg_ids:
            msg_id = msg_id_bytes.decode("ascii", errors="replace")
            if msg_id in processed:
                continue

            # Fetch headers
            try:
                _, msg_data = mail.fetch(msg_id_bytes, "(BODY.PEEK[HEADER] BODY.PEEK[1]<0.500>)")
            except Exception as exc:  # noqa: BLE001
                _log(f"fetch_failed msg_id={msg_id} error={exc}")
                continue

            if not msg_data or not msg_data[0]:
                continue

            try:
                header_data = msg_data[0][1]
                if isinstance(header_data, bytes):
                    msg = email.message_from_bytes(header_data)
                else:
                    continue

                sender = _decode(msg.get("From", ""))
                subject = _decode(msg.get("Subject", ""))
                body_preview = ""
                if len(msg_data) > 1 and msg_data[1] and len(msg_data[1]) > 1:
                    body_raw = msg_data[1][1]
                    if isinstance(body_raw, bytes):
                        body_preview = body_raw.decode("utf-8", errors="replace")[:500]

                if _matches_trigger(sender, subject):
                    _log(f"trigger_match sender={sender!r} subject={subject!r}")
                    if _send_alert(sender, subject, body_preview):
                        processed.add(msg_id)
                        new_alerts += 1
                else:
                    # Не match — всё равно skip чтобы не пере-обрабатывать
                    processed.add(msg_id)
            except Exception as exc:  # noqa: BLE001
                _log(f"process_failed msg_id={msg_id} error={exc}")

        _log(f"poll_done new_alerts={new_alerts}")
    finally:
        try:
            mail.close()
            mail.logout()
        except Exception:  # noqa: BLE001
            pass

    state["processed_ids"] = sorted(processed)
    _save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
