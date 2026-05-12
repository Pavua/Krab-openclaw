#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Личный помощник для аккуратных напоминаний отцу.

Зачем нужен:
- читает локальный приватный конфиг из `~/.openclaw/krab_runtime_state`;
- может read-only посмотреть недавний iMessage-диалог через `chat.db`;
- собирает короткий черновик Telegram/iMessage без давления и манипуляций;
- по умолчанию работает только в dry-run, чтобы случайно не отправить сообщение.

Связь с проектом:
- использует общий JSON/audit контракт `scripts/agent_tools`;
- реальную отправку в Telegram делает через Pyrogram userbot session `kraab`;
- iMessage отправку делегирует существующему `krab_send_imessage.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    SESSION_DIR,
    SESSION_NAME,
    emit_error,
    emit_json,
    get_telegram_credentials,
)
from _multi_channel_helpers import (  # noqa: E402
    RUNTIME_STATE_DIR,
    audit_event,
    first_time_gate,
    is_hard_blocked,
    remember_recipient,
)

SCRIPT = "krab_father_reminder.py"
CONFIG_PATH = RUNTIME_STATE_DIR / "father_reminder.json"
STATE_PATH = RUNTIME_STATE_DIR / "father_reminder_state.json"
CHAT_DB_PATH = Path("~/Library/Messages/chat.db").expanduser()
TELEGRAM_KNOWN_PATH = RUNTIME_STATE_DIR / "telegram_known_personal_recipients.json"


@dataclass
class FatherReminderConfig:
    """Приватная конфигурация одного адресата."""

    telegram_username: str = ""
    imessage_handle: str = ""
    objective: str = ""
    cadence_days: int = 3
    tone: str = "коротко, уважительно, без давления"
    dry_run_default: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FatherReminderConfig":
        return cls(
            telegram_username=str(payload.get("telegram_username") or "").strip(),
            imessage_handle=str(payload.get("imessage_handle") or "").strip(),
            objective=str(payload.get("objective") or "").strip(),
            cadence_days=max(1, int(payload.get("cadence_days") or 3)),
            tone=str(payload.get("tone") or "коротко, уважительно, без давления").strip(),
            dry_run_default=bool(payload.get("dry_run_default", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "telegram_username": self.telegram_username,
            "imessage_handle": self.imessage_handle,
            "objective": self.objective,
            "cadence_days": self.cadence_days,
            "tone": self.tone,
            "dry_run_default": self.dry_run_default,
        }


def _redact(value: str) -> str:
    """Маскирует приватные контакты в статусе и ошибках."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        return raw[:3] + "***"
    if len(raw) <= 4:
        return "***"
    return raw[:3] + "***" + raw[-2:]


def _normalize_phone_handle(value: str) -> str:
    """Приводит телефонный iMessage handle к формату Messages.app."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "@" in raw:
        return raw
    prefix = "+" if raw.startswith("+") else ""
    digits = re.sub(r"\D+", "", raw)
    return f"{prefix}{digits}" if digits else raw


def _load_config() -> FatherReminderConfig:
    if not CONFIG_PATH.is_file():
        return FatherReminderConfig()
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return FatherReminderConfig()
        return FatherReminderConfig.from_dict(payload)
    except (OSError, json.JSONDecodeError, ValueError):
        return FatherReminderConfig()


def _save_config(cfg: FatherReminderConfig) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def _load_state() -> dict[str, Any]:
    """Читает runtime-состояние без падения при битом или отсутствующем файле."""
    if not STATE_PATH.is_file():
        return {}
    try:
        payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(payload: dict[str, Any]) -> None:
    """Атомарно сохраняет журнал последнего напоминания."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _now_iso() -> str:
    """Единый формат времени для state/audit."""
    return datetime.now(tz=timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime | None:
    """Разбирает ISO timestamp, который сам же записывает этот tool."""
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _days_since(value: str) -> float | None:
    """Возвращает возраст timestamp в днях."""
    parsed = _parse_iso(value)
    if not parsed:
        return None
    delta = datetime.now(tz=timezone.utc) - parsed
    return delta.total_seconds() / 86_400


def _open_messages_db() -> sqlite3.Connection:
    """Открывает Messages `chat.db` строго read-only."""
    if not CHAT_DB_PATH.exists():
        raise FileNotFoundError(f"chat.db not found: {CHAT_DB_PATH}")
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("SELECT 1 FROM message LIMIT 1").fetchone()
    return conn


def _cocoa_to_iso(cocoa_ns: int | None) -> str:
    """Конвертирует timestamp Messages.app в ISO UTC."""
    if not cocoa_ns:
        return ""
    unix_ts = (int(cocoa_ns) / 1_000_000_000) + 978_307_200
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()


def _extract_attributed_body_text(blob: bytes | None) -> str:
    """Достаёт plain text из `attributedBody` Messages.app без записи в БД.

    Breadcrumb: современные iMessage часто держат `message.text = NULL`, а
    человекочитаемый текст лежит внутри legacy typedstream `NSAttributedString`.
    Это не полноценный декодер typedstream: мы безопасно ищем UTF-8 payload
    между `NSString` и следующим объектом, потому что для напоминаний нужен
    текст, а не стили, реакции и служебные атрибуты.
    """
    if not blob:
        return ""
    marker_at = blob.find(b"NSString\x01")
    if marker_at < 0:
        return ""
    start_base = marker_at + len(b"NSString\x01")
    end_candidates = [
        pos
        for pattern in (b"\x86\x84\x02", b"\x86\x84", b"\x92\x84\x84\x84\x0cNSDictionary")
        if (pos := blob.find(pattern, start_base)) >= 0
    ]
    end = min(end_candidates) if end_candidates else min(len(blob), start_base + 4000)

    best = ""
    for start in range(start_base, min(start_base + 24, end)):
        for candidate_end in range(end, max(start, end - 24), -1):
            try:
                decoded = blob[start:candidate_end].decode("utf-8")
            except UnicodeDecodeError:
                continue
            text = "".join(ch for ch in decoded if ch in "\n\t" or ord(ch) >= 32).strip()
            while text and not (text[0].isalnum() or "\u0400" <= text[0] <= "\u052f"):
                text = text[1:].lstrip()
            while (
                len(text) > 1
                and text[0].isascii()
                and text[0].isalnum()
                and "\u0400" <= text[1] <= "\u052f"
            ):
                text = text[1:].lstrip()
            if any(ch.isalpha() for ch in text) and len(text) > len(best):
                best = text
            break
    return best


def _read_imessage_history(handle: str, limit: int) -> list[dict[str, Any]]:
    """Возвращает последние сообщения с конкретным handle без записи в БД."""
    normalized_handle = _normalize_phone_handle(handle)
    if not normalized_handle:
        return []
    conn = _open_messages_db()
    try:
        rows = conn.execute(
            """
            SELECT m.ROWID as id, m.text, m.attributedBody, m.date, m.is_from_me, h.id as handle
              FROM message m
              JOIN handle h ON h.ROWID = m.handle_id
             WHERE h.id = ?
             ORDER BY m.date DESC
             LIMIT ?
            """,
            (normalized_handle, int(limit)),
        ).fetchall()
    finally:
        conn.close()

    result: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "").strip() or _extract_attributed_body_text(
            row["attributedBody"]
        )
        if not text:
            continue
        result.append(
            {
                "id": int(row["id"]),
                "date": _cocoa_to_iso(row["date"]),
                "from_me": bool(row["is_from_me"]),
                "text": text,
            }
        )
    return result


def _compact_context(messages: list[dict[str, Any]], max_items: int = 8) -> list[dict[str, Any]]:
    """Укорачивает историю до безопасного контекста для отчёта."""
    compact: list[dict[str, Any]] = []
    for item in list(reversed(messages[:max_items])):
        text = str(item.get("text") or "").replace("\n", " ").strip()
        if len(text) > 220:
            text = text[:217].rstrip() + "..."
        compact.append(
            {
                "date": item.get("date") or "",
                "from": "me" if item.get("from_me") else "father",
                "text": text,
            }
        )
    return compact


def _draft_message(cfg: FatherReminderConfig, explicit_goal: str = "") -> str:
    """Собирает мягкий короткий текст напоминания."""
    goal = (explicit_goal or cfg.objective or "[конкретная просьба]").strip()
    # Breadcrumb: держим формулу без обвинений, потому что задача — получить действие, а не спор.
    return (
        "Пап, привет. Напоминаю про "
        f"{goal}. Мне важно закрыть это в ближайшее время. "
        "Скажи, пожалуйста, когда реально сможешь сделать?"
    )


async def _send_telegram(username: str, text: str) -> dict[str, Any]:
    """Отправляет Telegram-сообщение от userbot session."""
    from pyrogram import Client  # type: ignore

    api_id, api_hash = get_telegram_credentials()
    client = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        workdir=str(SESSION_DIR),
        no_updates=True,
    )
    async with client:
        msg = await client.send_message(username, text)
        return {
            "ok": True,
            "message_id": int(msg.id),
            "chat_id": str(getattr(msg.chat, "id", "")),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }


def cmd_init(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _load_config()
    if args.telegram_username:
        cfg.telegram_username = args.telegram_username.strip()
    if args.imessage_handle:
        cfg.imessage_handle = args.imessage_handle.strip()
    if args.objective:
        cfg.objective = args.objective.strip()
    if args.cadence_days:
        cfg.cadence_days = max(1, int(args.cadence_days))
    _save_config(cfg)
    return {
        "ok": True,
        "config_path": str(CONFIG_PATH),
        "telegram_username": _redact(cfg.telegram_username),
        "imessage_handle": _redact(cfg.imessage_handle),
        "objective_set": bool(cfg.objective),
        "dry_run_default": cfg.dry_run_default,
    }


def cmd_status(_args: argparse.Namespace) -> dict[str, Any]:
    cfg = _load_config()
    state = _load_state()
    last_sent_at = str(state.get("last_sent_at") or "")
    days_since = _days_since(last_sent_at)
    return {
        "ok": True,
        "config_exists": CONFIG_PATH.is_file(),
        "config_path": str(CONFIG_PATH),
        "state_path": str(STATE_PATH),
        "telegram_username": _redact(cfg.telegram_username),
        "imessage_handle": _redact(cfg.imessage_handle),
        "objective_set": bool(cfg.objective),
        "cadence_days": cfg.cadence_days,
        "dry_run_default": cfg.dry_run_default,
        "last_sent_at": last_sent_at,
        "days_since_last_send": None if days_since is None else round(days_since, 2),
    }


def cmd_analyze(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _load_config()
    handle = args.imessage_handle or cfg.imessage_handle
    if not handle:
        return {"ok": False, "error": "imessage_handle_not_configured"}
    try:
        messages = _read_imessage_history(handle, args.limit)
    except sqlite3.OperationalError as exc:
        return {
            "ok": False,
            "error": "messages_db_unavailable",
            "detail": str(exc),
            "hint": "Нужен Full Disk Access для процесса, который запускает скрипт.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "count": len(messages),
        "handle": _redact(handle),
        "recent_context": _compact_context(messages, max_items=args.context_items),
        "draft": _draft_message(cfg, args.objective),
    }


def cmd_draft(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _load_config()
    return {"ok": True, "draft": _draft_message(cfg, args.objective)}


def cmd_send(args: argparse.Namespace) -> dict[str, Any]:
    cfg = _load_config()
    text = args.text or _draft_message(cfg, args.objective)
    dry_run = args.dry_run or (cfg.dry_run_default and not args.confirm_send)
    if args.channel == "telegram":
        username = args.telegram_username or cfg.telegram_username
        if not username:
            return {"ok": False, "error": "telegram_username_not_configured"}
        blocked, reason = is_hard_blocked(username)
        if blocked:
            audit_event("telegram", username, "father_reminder_blocked", False, {"reason": reason})
            return {"ok": False, "error": f"hard_blocked: {reason}"}
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "channel": "telegram",
                "recipient": _redact(username),
                "text": text,
            }
        allowed, gate_reason = first_time_gate(
            TELEGRAM_KNOWN_PATH,
            username,
            args.first_time_confirm,
            args.owner_token,
        )
        if not allowed:
            audit_event(
                "telegram",
                username,
                "father_reminder_first_time_blocked",
                False,
                {"reason": gate_reason},
            )
            return {
                "ok": False,
                "error": "first_time_no_confirm",
                "hint": "Первый реальный Telegram-send требует --first-time-confirm или owner token.",
            }
        try:
            res = asyncio.run(_send_telegram(username, text))
        except Exception as exc:  # noqa: BLE001
            audit_event(
                "telegram", username, "father_reminder_send_failed", False, {"err": str(exc)}
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        remember_recipient(TELEGRAM_KNOWN_PATH, username, {"channel": "telegram_userbot"})
        audit_event(
            "telegram",
            username,
            "father_reminder_sent",
            True,
            {"message_id": res.get("message_id")},
        )
        return {
            **res,
            "channel": "telegram",
            "recipient": _redact(username),
            "gate_reason": gate_reason,
        }

    handle = args.imessage_handle or cfg.imessage_handle
    if not handle:
        return {"ok": False, "error": "imessage_handle_not_configured"}
    blocked, reason = is_hard_blocked(handle)
    if blocked:
        audit_event("imessage", handle, "father_reminder_blocked", False, {"reason": reason})
        return {"ok": False, "error": f"hard_blocked: {reason}"}
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "krab_send_imessage.py"),
        "--to",
        handle,
        "--text",
        text,
    ]
    if dry_run or args.first_time_confirm:
        # Breadcrumb: dry-run можно запускать без шума, но real send для нового
        # адресата всё равно требует явный `--first-time-confirm`.
        cmd.append("--first-time-confirm")
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)  # noqa: S603
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        payload = {"ok": False, "error": proc.stderr.strip() or proc.stdout.strip()}
    payload["channel"] = "imessage"
    payload["recipient"] = _redact(handle)
    return payload


def cmd_run_due(args: argparse.Namespace) -> dict[str, Any]:
    """Проверяет расписание и отправляет напоминание только если оно действительно due."""
    cfg = _load_config()
    state = _load_state()
    last_sent_at = str(state.get("last_sent_at") or "")
    days_since = _days_since(last_sent_at)
    cadence_days = max(1, int(args.cadence_days or cfg.cadence_days))

    if days_since is not None and days_since < cadence_days and not args.force:
        return {
            "ok": True,
            "sent": False,
            "reason": "not_due",
            "cadence_days": cadence_days,
            "last_sent_at": last_sent_at,
            "days_since_last_send": round(days_since, 2),
        }

    # Breadcrumb: run-due использует тот же cmd_send, чтобы не расходились
    # first-time guard, hard-block и аудит между ручной и scheduled отправкой.
    send_args = argparse.Namespace(
        channel=args.channel,
        telegram_username=args.telegram_username,
        imessage_handle=args.imessage_handle,
        objective=args.objective,
        text=args.text,
        dry_run=args.dry_run,
        confirm_send=args.confirm_send,
        first_time_confirm=args.first_time_confirm,
        owner_token=args.owner_token,
    )
    result = cmd_send(send_args)
    if result.get("ok") and not result.get("dry_run"):
        state.update(
            {
                "last_sent_at": _now_iso(),
                "last_channel": args.channel,
                "last_recipient": result.get("recipient"),
                "last_message_id": result.get("message_id"),
            }
        )
        _save_state(state)
        result["sent"] = True
    else:
        result["sent"] = False
    result["cadence_days"] = cadence_days
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Father reminder assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser("init")
    init.add_argument("--telegram-username", default="")
    init.add_argument("--imessage-handle", default="")
    init.add_argument("--objective", default="")
    init.add_argument("--cadence-days", type=int, default=3)

    sub.add_parser("status")

    analyze = sub.add_parser("analyze")
    analyze.add_argument("--imessage-handle", default="")
    analyze.add_argument("--objective", default="")
    analyze.add_argument("--limit", type=int, default=40)
    analyze.add_argument("--context-items", type=int, default=8)

    draft = sub.add_parser("draft")
    draft.add_argument("--objective", default="")

    send = sub.add_parser("send")
    send.add_argument("--channel", choices=["telegram", "imessage"], default="telegram")
    send.add_argument("--telegram-username", default="")
    send.add_argument("--imessage-handle", default="")
    send.add_argument("--objective", default="")
    send.add_argument("--text", default="")
    send.add_argument("--dry-run", action="store_true")
    send.add_argument("--confirm-send", action="store_true")
    send.add_argument("--first-time-confirm", action="store_true")
    send.add_argument("--owner-token", default=None)

    run_due = sub.add_parser("run-due")
    run_due.add_argument("--channel", choices=["telegram", "imessage"], default="telegram")
    run_due.add_argument("--telegram-username", default="")
    run_due.add_argument("--imessage-handle", default="")
    run_due.add_argument("--objective", default="")
    run_due.add_argument("--text", default="")
    run_due.add_argument("--cadence-days", type=int, default=0)
    run_due.add_argument("--dry-run", action="store_true")
    run_due.add_argument("--confirm-send", action="store_true")
    run_due.add_argument("--first-time-confirm", action="store_true")
    run_due.add_argument("--owner-token", default=None)
    run_due.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "status": cmd_status,
        "analyze": cmd_analyze,
        "draft": cmd_draft,
        "send": cmd_send,
        "run-due": cmd_run_due,
    }
    try:
        result = handlers[args.cmd](args)
    except Exception as exc:  # noqa: BLE001
        return emit_error(f"{type(exc).__name__}: {exc}", SCRIPT, sys.argv[1:])
    emit_json(result, SCRIPT, sys.argv[1:])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
