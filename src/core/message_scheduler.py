# -*- coding: utf-8 -*-
"""
Отложенные сообщения через Pyrogram schedule_date (MTProto-native).

Отличие от scheduler.py (reminders):
- Не использует asyncio.sleep — сервер Telegram сам держит задачу.
- Сообщение уходит даже если Krab оффлайн (в рамках TTL Telegram).
- Хранит pending задачи локально для !schedule list/cancel.

Команды:
    !schedule HH:MM <текст>   — отправить в указанное время (сегодня/завтра)
    !schedule +Nm <текст>     — через N минут
    !schedule +Nh <текст>     — через N часов
    !schedule list            — список запланированных сообщений
    !schedule cancel <id>     — отменить (удалить scheduled message)
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import config
from .logger import get_logger

logger = get_logger(__name__)

# Паттерны парсинга времени
_PATTERN_HHMM = re.compile(r"^(\d{1,2}):(\d{2})$")
_PATTERN_PLUS_MIN = re.compile(r"^\+(\d+)m$", re.IGNORECASE)
_PATTERN_PLUS_HOUR = re.compile(r"^\+(\d+)h$", re.IGNORECASE)

# Минимальный отступ для schedule_date (Telegram требует >= ~10-20 сек)
_MIN_SCHEDULE_SECONDS = 30


def _now_local() -> datetime:
    """Текущее локальное время (timezone-aware)."""
    return datetime.now().astimezone()


def parse_schedule_spec(spec: str) -> datetime:
    """
    Парсит time_spec для !schedule в абсолютную datetime (timezone-aware).

    Форматы:
        HH:MM       — сегодня или завтра (если время уже прошло)
        +Nm         — через N минут (минимум 1)
        +Nh         — через N часов

    Raises:
        ValueError — если формат не распознан или время в прошлом.
    """
    raw = str(spec or "").strip()
    now = _now_local()

    m = _PATTERN_PLUS_MIN.match(raw)
    if m:
        minutes = int(m.group(1))
        if minutes < 1:
            raise ValueError("minutes_must_be_positive")
        return now + timedelta(minutes=minutes)

    m = _PATTERN_PLUS_HOUR.match(raw)
    if m:
        hours = int(m.group(1))
        if hours < 1:
            raise ValueError("hours_must_be_positive")
        return now + timedelta(hours=hours)

    m = _PATTERN_HHMM.match(raw)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("invalid_hhmm_range")
        due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now:
            due += timedelta(days=1)
        return due

    raise ValueError(f"unrecognized_schedule_spec: {raw!r}")


def split_schedule_input(raw: str) -> tuple[str, str]:
    """
    Делит аргумент !schedule на time_spec и текст.

    Примеры:
        "14:30 Привет"        → ("14:30", "Привет")
        "+30m Напомни позвонить" → ("+30m", "Напомни позвонить")
        "list"               → ("list", "")
        "cancel abc123"      → ("cancel", "abc123")
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""

    parts = text.split(maxsplit=1)
    spec = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    # Служебные команды (нормализуем русские алиасы к английским ключам)
    _aliases = {"list": "list", "cancel": "cancel", "список": "list", "отмена": "cancel"}
    if spec.lower() in _aliases:
        return _aliases[spec.lower()], rest

    return spec, rest


@dataclass
class ScheduledMsgRecord:
    """Локальная запись об отложенном сообщении."""

    record_id: str
    chat_id: str
    text: str
    schedule_time_iso: str  # когда должно уйти
    tg_message_id: int  # message_id в Telegram (для cancel)
    created_at_iso: str
    status: str = "pending"  # pending | cancelled | sent | unknown

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScheduledMsgRecord":
        return cls(
            record_id=str(d.get("record_id") or ""),
            chat_id=str(d.get("chat_id") or ""),
            text=str(d.get("text") or ""),
            schedule_time_iso=str(d.get("schedule_time_iso") or ""),
            tg_message_id=int(d.get("tg_message_id") or 0),
            created_at_iso=str(d.get("created_at_iso") or ""),
            status=str(d.get("status") or "pending"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "chat_id": self.chat_id,
            "text": self.text,
            "schedule_time_iso": self.schedule_time_iso,
            "tg_message_id": self.tg_message_id,
            "created_at_iso": self.created_at_iso,
            "status": self.status,
        }


class MessageSchedulerStore:
    """
    Хранилище записей об отложенных сообщениях (JSON-файл).
    Не держит state в памяти — каждый вызов читает/пишет файл.
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or (
            config.BASE_DIR / "data" / "message_scheduler" / "scheduled.json"
        )

    # ---------- внутренние методы ----------

    def _load_all(self) -> dict[str, ScheduledMsgRecord]:
        if not self.storage_path.exists():
            return {}
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            rows = payload.get("records", []) if isinstance(payload, dict) else []
            return {
                r.record_id: r
                for item in rows
                if isinstance(item, dict)
                for r in [ScheduledMsgRecord.from_dict(item)]
                if r.record_id
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("msg_scheduler_load_failed", error=str(exc))
            return {}

    def _save_all(self, records: dict[str, ScheduledMsgRecord]) -> None:
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [r.to_dict() for r in records.values()]
            payload = {
                "updated_at": _now_local().isoformat(),
                "records": rows,
            }
            tmp = self.storage_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.storage_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("msg_scheduler_save_failed", error=str(exc))

    # ---------- публичный API ----------

    def add(self, *, chat_id: str, text: str, schedule_time: datetime, tg_message_id: int) -> str:
        """Добавляет запись. Возвращает record_id (8 hex chars)."""
        records = self._load_all()
        record_id = uuid.uuid4().hex[:8]
        records[record_id] = ScheduledMsgRecord(
            record_id=record_id,
            chat_id=str(chat_id),
            text=str(text),
            schedule_time_iso=schedule_time.astimezone().isoformat(),
            tg_message_id=int(tg_message_id),
            created_at_iso=_now_local().isoformat(),
            status="pending",
        )
        self._save_all(records)
        return record_id

    def list_pending(self, *, chat_id: str | None = None) -> list[ScheduledMsgRecord]:
        """Возвращает pending-записи, отсортированные по времени."""
        records = self._load_all()
        result = [
            r
            for r in records.values()
            if r.status == "pending" and (chat_id is None or r.chat_id == str(chat_id))
        ]
        result.sort(key=lambda r: r.schedule_time_iso)
        return result

    def get(self, record_id: str) -> ScheduledMsgRecord | None:
        """Возвращает запись по record_id."""
        return self._load_all().get(str(record_id))

    def mark_cancelled(self, record_id: str) -> bool:
        """Помечает запись как cancelled. Возвращает True если нашёл."""
        records = self._load_all()
        rec = records.get(str(record_id))
        if rec is None:
            return False
        rec.status = "cancelled"
        self._save_all(records)
        return True


# Singleton хранилища (инициализируется один раз)
msg_scheduler_store = MessageSchedulerStore()


# ---------- Форматирование ----------


def _format_schedule_time(iso: str) -> str:
    """Форматирует ISO-время для отображения в Telegram."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d.%m %H:%M")
    except Exception:  # noqa: BLE001
        return iso


def format_scheduled_list(records: list[ScheduledMsgRecord]) -> str:
    """Форматирует список отложенных сообщений для ответа в Telegram."""
    if not records:
        return "📭 Нет запланированных сообщений."

    lines = ["📅 **Запланированные сообщения:**\n"]
    for rec in records:
        preview = rec.text[:60] + ("…" if len(rec.text) > 60 else "")
        when = _format_schedule_time(rec.schedule_time_iso)
        lines.append(f"• `{rec.record_id}` — {when}\n  _{preview}_")

    lines.append("\nДля отмены: `!schedule cancel <id>`")
    return "\n".join(lines)
