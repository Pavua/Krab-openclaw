#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wave 117: ежедневный reminder про Google Sales POC по Anthropic Vertex quota.

Контекст: Session 45 closed — Anthropic Vertex quota pending (cases #70886393,
#70886496). Contact Sales submitted 12 May 2026. Если POC не отвечает ≥ 7 дней
после первого detected blocked-snapshot, нужно re-submit / напомнить.

Логика:
1. Читаем `~/.openclaw/krab_runtime_state/anthropic_vertex_status.json`
   (продюсит Wave 104 preflight). Поле `vertex_quota_status`:
   - ok → POC approved, ничего не делаем (action=approved);
   - blocked → проверяем сколько дней pending;
   - unknown / нет файла → skip.
2. Отдельно ведём `~/.openclaw/krab_runtime_state/google_sales_followup_state.json`:
   `{first_blocked_at, last_reminder_at, reminders_sent}`. На первом blocked
   зафиксируем `first_blocked_at` = текущий timestamp.
3. Если days_pending ≥ THRESHOLD_DAYS (default 7) → action=send_followup +
   опциональный Telegram DM owner. throttling: не чаще раз в 7 дней.
4. STDOUT (JSON):
   `{timestamp, days_pending, action, status}` — для logfile/dashboard parsing.

Env:
- KRAB_GOOGLE_SALES_FOLLOWUP_THRESHOLD_DAYS (default 7),
- KRAB_GOOGLE_SALES_FOLLOWUP_ALERT_TELEGRAM (default 1),
- KRAB_GOOGLE_SALES_FOLLOWUP_REMINDER_INTERVAL_DAYS (default 7),
- OPENCLAW_TELEGRAM_BOT_TOKEN, OWNER_USER_IDS.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

# ─── .env loader ────────────────────────────────────────────────────────────


def _load_dotenv() -> None:
    """Прокидывает .env в os.environ для LaunchAgent-сценария."""
    env_file = os.environ.get("ENV_FILE", str(Path(__file__).parent.parent / ".env"))
    path = Path(env_file)
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()


# ─── Конфигурация ───────────────────────────────────────────────────────────

THRESHOLD_DAYS: int = int(os.environ.get("KRAB_GOOGLE_SALES_FOLLOWUP_THRESHOLD_DAYS", "7"))
REMINDER_INTERVAL_DAYS: int = int(
    os.environ.get("KRAB_GOOGLE_SALES_FOLLOWUP_REMINDER_INTERVAL_DAYS", "7")
)
ALERT_TELEGRAM: bool = os.environ.get("KRAB_GOOGLE_SALES_FOLLOWUP_ALERT_TELEGRAM", "1").strip() in {
    "1",
    "true",
    "yes",
    "on",
}

TG_TOKEN: str | None = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN")
_OWNER_LIST: str = os.environ.get("OWNER_USER_IDS", "")
TG_OWNER: str = (
    _OWNER_LIST.split(",")[0].strip() if _OWNER_LIST else os.environ.get("OWNER_NOTIFY_CHAT_ID", "")
)

DEFAULT_STATE_DIR = Path.home() / ".openclaw" / "krab_runtime_state"
STATE_DIR: Path = Path(
    os.environ.get("KRAB_GOOGLE_SALES_FOLLOWUP_STATE_DIR", str(DEFAULT_STATE_DIR))
)
PREFLIGHT_STATUS_FILE: Path = STATE_DIR / "anthropic_vertex_status.json"
FOLLOWUP_STATE_FILE: Path = STATE_DIR / "google_sales_followup_state.json"
LOG_FILE: Path = STATE_DIR / "google_sales_followup.log"


# ─── Логирование ────────────────────────────────────────────────────────────


def _log_to(file: Path, msg: str) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with file.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def log_info(msg: str) -> None:
    _log_to(LOG_FILE, msg)


def log_err(msg: str) -> None:
    _log_to(LOG_FILE, f"ERR {msg}")
    print(f"ERR {msg}", file=sys.stderr, flush=True)


# ─── IO helpers ─────────────────────────────────────────────────────────────


def load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log_err(f"load error_type={type(exc).__name__} path={path} error={exc}")
        return None


def save_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ─── Telegram ───────────────────────────────────────────────────────────────


def send_telegram_alert(
    text: str,
    *,
    client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> bool:
    if not TG_TOKEN or not TG_OWNER:
        log_err("telegram_alert missing_creds")
        return False
    payload = {
        "chat_id": TG_OWNER,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=payload)
        if resp.status_code != 200:
            log_err(f"telegram_alert status={resp.status_code}")
            return False
        return True
    except httpx.RequestError as exc:
        log_err(f"telegram_alert error_type={type(exc).__name__} error={exc}")
        return False
    finally:
        if owns_client and client is not None:
            client.close()


# ─── Main flow ──────────────────────────────────────────────────────────────


def run_followup(
    *,
    now: datetime | None = None,
    preflight_path: Path = PREFLIGHT_STATUS_FILE,
    followup_state_path: Path = FOLLOWUP_STATE_FILE,
    threshold_days: int = THRESHOLD_DAYS,
    reminder_interval_days: int = REMINDER_INTERVAL_DAYS,
    alert_telegram: bool = ALERT_TELEGRAM,
    telegram_sender: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Решает: skip / send_followup / approved. Persists followup state.

    Returns: dict с ключами timestamp, days_pending, action, status.
    """
    now = now or datetime.now(timezone.utc)
    preflight = load_json(preflight_path)
    state = load_json(followup_state_path) or {}

    snapshot: dict[str, Any] = {
        "timestamp": now.isoformat(),
        "days_pending": 0,
        "action": "skip",
        "status": None,
    }

    # 1) Файл preflight отсутствует — нечего проверять.
    if preflight is None:
        snapshot["action"] = "skip"
        snapshot["status"] = "missing_state_file"
        log_info("skip reason=missing_preflight_state_file")
        return snapshot

    vertex_status = preflight.get("vertex_quota_status")
    snapshot["status"] = vertex_status

    # 2) Approved → сброс followup state, action=approved.
    if vertex_status == "ok":
        snapshot["action"] = "approved"
        # Сбросим first_blocked_at чтобы при будущем blocked отсчёт начался заново.
        new_state = {
            "first_blocked_at": None,
            "last_reminder_at": state.get("last_reminder_at"),
            "reminders_sent": state.get("reminders_sent", 0),
            "cleared_at": now.isoformat(),
        }
        save_json(new_state, followup_state_path)
        log_info("approved vertex_quota=ok — followup state cleared")
        return snapshot

    # 3) unknown → skip (Wave 104 не смог классифицировать).
    if vertex_status != "blocked":
        snapshot["action"] = "skip"
        log_info(f"skip vertex_status={vertex_status}")
        return snapshot

    # 4) blocked: считаем days_pending от first_blocked_at.
    first_blocked_at = parse_iso(state.get("first_blocked_at"))
    if first_blocked_at is None:
        # Впервые видим blocked → фиксируем сейчас.
        first_blocked_at = now
        state["first_blocked_at"] = now.isoformat()
        state.setdefault("reminders_sent", 0)
        save_json(state, followup_state_path)
        snapshot["days_pending"] = 0
        snapshot["action"] = "skip"
        log_info("first_blocked_seen — followup state initialized")
        return snapshot

    delta = now - first_blocked_at
    days_pending = max(0, delta.days)
    snapshot["days_pending"] = days_pending

    # 5) Не дожили до threshold → skip.
    if days_pending < threshold_days:
        snapshot["action"] = "skip"
        log_info(f"skip days_pending={days_pending} threshold={threshold_days}")
        return snapshot

    # 6) Throttle: не чаще раз в reminder_interval_days.
    last_reminder_at = parse_iso(state.get("last_reminder_at"))
    if last_reminder_at is not None:
        since_last = (now - last_reminder_at).days
        if since_last < reminder_interval_days:
            snapshot["action"] = "skip"
            log_info(
                f"throttled since_last_reminder={since_last} interval={reminder_interval_days}"
            )
            return snapshot

    # 7) Шлём reminder.
    snapshot["action"] = "send_followup"
    text = (
        "<b>Wave 117: Google Sales POC reminder</b>\n"
        f"Anthropic Vertex quota всё ещё <code>blocked</code>.\n"
        f"Дней pending: <b>{days_pending}</b>.\n"
        "Рассмотрите re-submission или ping cases "
        "#70886393 / #70886496."
    )
    sender = telegram_sender or send_telegram_alert
    if alert_telegram:
        sent_ok = sender(text)
        log_info(f"reminder_sent days_pending={days_pending} ok={sent_ok}")
    else:
        log_info(f"reminder_dry_run days_pending={days_pending}")

    state["last_reminder_at"] = now.isoformat()
    state["reminders_sent"] = int(state.get("reminders_sent", 0)) + 1
    save_json(state, followup_state_path)
    return snapshot


def main() -> int:
    try:
        result = run_followup()
    except Exception as exc:  # pragma: no cover - defensive
        log_err(f"followup_fatal error_type={type(exc).__name__} error={exc}")
        return 1
    # Печатаем JSON в stdout — пойдёт в LaunchAgent stdout-лог.
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
