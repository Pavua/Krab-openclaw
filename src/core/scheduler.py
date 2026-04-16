# -*- coding: utf-8 -*-
"""
Легковесный scheduler для Krab (без внешних зависимостей).

Зачем:
1) Восстановить реальное фоновое выполнение задач (reminders/once jobs) в userbot-контуре.
2) Убрать ситуацию "бот обещал выполнить позже, но ничего не произошло".
3) Держать реализацию максимально надежной: asyncio-only, с сохранением pending reminders на диск.

Связь с проектом:
- используется из command handlers (`!remind`, `!reminders`, `!rm_remind`, `!cronstatus`);
- sender callback привязывается из userbot_bridge после успешного старта Telegram-клиента.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import config
from .inbox_service import inbox_service
from .logger import get_logger

logger = get_logger(__name__)

_TZ = datetime.now().astimezone().tzinfo
_SHORT_DELAY_PATTERN = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_RUS_DELAY_PATTERN = re.compile(
    r"^\s*через\s+(\d+)\s*(сек(?:унд[ауы]?|)|мин(?:ут[ауы]?|)|час(?:а|ов)?|дн(?:я|ей)?)\s*$",
    re.IGNORECASE,
)
_AT_TIME_PATTERN = re.compile(r"^\s*(?:в\s*|at\s+)?(\d{1,2}):(\d{2})\s*$", re.IGNORECASE)
_DATE_TIME_ISO_PATTERN = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})\s*$")
_DATE_TIME_DDMM_PATTERN = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s+(\d{1,2}):(\d{2})\s*$")
# "tomorrow 9:00" / "завтра 9:00"
_TOMORROW_TIME_PATTERN = re.compile(
    r"^\s*(?:tomorrow|завтра)\s+(\d{1,2}):(\d{2})\s*$",
    re.IGNORECASE,
)
# "in 30m" / "in 2h" / "in 45s" / "in 1d" (английский inline-стиль)
_IN_DELAY_PATTERN = re.compile(r"^\s*in\s+(\d+)\s*([smhd])\s*$", re.IGNORECASE)
# "in N minutes/hours/days/seconds" (English long form)
_IN_DELAY_LONG_EN_PATTERN = re.compile(
    r"^\s*in\s+(\d+)\s*(second|seconds|minute|minutes|hour|hours|day|days)\s*$",
    re.IGNORECASE,
)
# "через N минут" (already handled above, but also used in split_reminder_input)
# "at HH:MM" — handled by _AT_TIME_PATTERN (supports "at" prefix)
# "N минут/часов/дней" без "через" (короткая русская форма)
_RUS_DELAY_SHORT_PATTERN = re.compile(
    r"^\s*(\d+)\s*(сек(?:унд[ауы]?)?|мин(?:ут[ауы]?)?|час(?:а|ов)?|дн(?:я|ей)?)\s*$",
    re.IGNORECASE,
)


def _now_local() -> datetime:
    """Текущее локальное время (timezone-aware)."""
    return datetime.now().astimezone()


def split_reminder_input(raw: str) -> tuple[str, str]:
    """
    Делит ввод `!remind` на time_spec и reminder_text.

    Поддержка:
    - `10m | купить воду`  (pipe-разделитель)
    - `через 10 минут купить воду`
    - `в 18:30 позвонить`
    - `me in 30m купить молоко`  (английский "me in N<unit>")
    - `in 30m купить молоко`
    - `at 15:00 позвонить`
    - `tomorrow 9:00 встреча` / `завтра 9:00 встреча`
    - `in 2 hours проверить почту`
    """
    text = str(raw or "").strip()
    if not text:
        return "", ""

    if "|" in text:
        left, right = text.split("|", 1)
        return left.strip(), right.strip()

    # Стрипаем опциональное "me " в начале (английский стиль "remind me in ...")
    stripped = re.sub(r"^me\s+", "", text, flags=re.IGNORECASE)

    patterns = [
        # "через N ед. текст"
        re.compile(
            r"^(через\s+\d+\s*(?:сек(?:унд[ауы]?|)|мин(?:ут[ауы]?|)|час(?:а|ов)?|дн(?:я|ей)?))\s+(.+)$",
            re.IGNORECASE,
        ),
        # "in N minutes/hours/days/seconds текст"
        re.compile(
            r"^(in\s+\d+\s*(?:second|seconds|minute|minutes|hour|hours|day|days))\s+(.+)$",
            re.IGNORECASE,
        ),
        # "in Nm/h/s/d текст"
        re.compile(r"^(in\s+\d+\s*[smhd])\s+(.+)$", re.IGNORECASE),
        # "at HH:MM текст"
        re.compile(r"^(at\s+\d{1,2}:\d{2})\s+(.+)$", re.IGNORECASE),
        # "tomorrow HH:MM текст" / "завтра HH:MM текст"
        re.compile(
            r"^((?:tomorrow|завтра)\s+\d{1,2}:\d{2})\s+(.+)$",
            re.IGNORECASE,
        ),
        # "Nm/h/s/d текст"  (без in)
        re.compile(r"^(\d+\s*[smhd])\s+(.+)$", re.IGNORECASE),
        # "в HH:MM текст" / "HH:MM текст"
        re.compile(r"^((?:в\s*)?\d{1,2}:\d{2})\s+(.+)$", re.IGNORECASE),
        # "YYYY-MM-DD HH:MM текст"
        re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{1,2}:\d{2})\s+(.+)$", re.IGNORECASE),
        # "DD.MM HH:MM текст"
        re.compile(r"^(\d{1,2}\.\d{1,2}\s+\d{1,2}:\d{2})\s+(.+)$", re.IGNORECASE),
        # "N минут/часов/дней текст" (краткая рус. форма без "через")
        re.compile(
            r"^(\d+\s*(?:сек(?:унд[ауы]?)?|мин(?:ут[ауы]?)?|час(?:а|ов)?|дн(?:я|ей)?))\s+(.+)$",
            re.IGNORECASE,
        ),
    ]
    for pattern in patterns:
        match = pattern.match(stripped)
        if match:
            return match.group(1).strip(), match.group(2).strip()

    return "", text


def parse_due_time(spec: str, *, now: datetime | None = None) -> datetime:
    """
    Парсит time_spec в дату/время запуска (timezone-aware).
    """
    raw = str(spec or "").strip()
    if not raw:
        raise ValueError("time_spec_empty")

    now_local = (now or _now_local()).astimezone()

    short_match = _SHORT_DELAY_PATTERN.match(raw)
    if short_match:
        amount = int(short_match.group(1))
        unit = short_match.group(2).lower()
        scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return now_local + timedelta(seconds=amount * scale)

    rus_match = _RUS_DELAY_PATTERN.match(raw)
    if rus_match:
        amount = int(rus_match.group(1))
        unit_raw = rus_match.group(2).lower()
        if unit_raw.startswith("сек"):
            scale = 1
        elif unit_raw.startswith("мин"):
            scale = 60
        elif unit_raw.startswith("час"):
            scale = 3600
        else:
            scale = 86400
        return now_local + timedelta(seconds=amount * scale)

    at_match = _AT_TIME_PATTERN.match(raw)
    if at_match:
        hour = int(at_match.group(1))
        minute = int(at_match.group(2))
        due = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if due <= now_local:
            due += timedelta(days=1)
        return due

    iso_match = _DATE_TIME_ISO_PATTERN.match(raw)
    if iso_match:
        due = datetime(
            year=int(iso_match.group(1)),
            month=int(iso_match.group(2)),
            day=int(iso_match.group(3)),
            hour=int(iso_match.group(4)),
            minute=int(iso_match.group(5)),
            tzinfo=now_local.tzinfo or _TZ,
        )
        return due.astimezone(now_local.tzinfo or _TZ)

    ddmm_match = _DATE_TIME_DDMM_PATTERN.match(raw)
    if ddmm_match:
        due = datetime(
            year=now_local.year,
            month=int(ddmm_match.group(2)),
            day=int(ddmm_match.group(1)),
            hour=int(ddmm_match.group(3)),
            minute=int(ddmm_match.group(4)),
            tzinfo=now_local.tzinfo or _TZ,
        )
        if due <= now_local:
            due = due.replace(year=due.year + 1)
        return due

    # "tomorrow HH:MM" / "завтра HH:MM"
    tomorrow_match = _TOMORROW_TIME_PATTERN.match(raw)
    if tomorrow_match:
        hour = int(tomorrow_match.group(1))
        minute = int(tomorrow_match.group(2))
        due = (now_local + timedelta(days=1)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return due

    # "in 30m" / "in 2h" / "in 45s" / "in 1d"
    in_match = _IN_DELAY_PATTERN.match(raw)
    if in_match:
        amount = int(in_match.group(1))
        unit = in_match.group(2).lower()
        scale = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
        return now_local + timedelta(seconds=amount * scale)

    # "in 2 hours" / "in 30 minutes" / "in 5 seconds" / "in 1 day"
    in_long_match = _IN_DELAY_LONG_EN_PATTERN.match(raw)
    if in_long_match:
        amount = int(in_long_match.group(1))
        unit_raw = in_long_match.group(2).lower()
        if unit_raw.startswith("sec"):
            scale = 1
        elif unit_raw.startswith("min"):
            scale = 60
        elif unit_raw.startswith("hour"):
            scale = 3600
        else:
            scale = 86400
        return now_local + timedelta(seconds=amount * scale)

    # "5 минут" / "2 часа" / "30 секунд" (краткая рус. форма без "через")
    rus_short_match = _RUS_DELAY_SHORT_PATTERN.match(raw)
    if rus_short_match:
        amount = int(rus_short_match.group(1))
        unit_raw = rus_short_match.group(2).lower()
        if unit_raw.startswith("сек"):
            scale = 1
        elif unit_raw.startswith("мин"):
            scale = 60
        elif unit_raw.startswith("час"):
            scale = 3600
        else:
            scale = 86400
        return now_local + timedelta(seconds=amount * scale)

    raise ValueError("time_spec_parse_failed")


@dataclass
class ReminderRecord:
    """Запись reminder-задачи."""

    reminder_id: str
    chat_id: str
    text: str
    due_at_iso: str
    created_at_iso: str
    status: str = "scheduled"
    retries: int = 0
    fired_at_iso: str = ""
    last_error: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReminderRecord":
        return cls(
            reminder_id=str(payload.get("reminder_id") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            text=str(payload.get("text") or ""),
            due_at_iso=str(payload.get("due_at_iso") or ""),
            created_at_iso=str(payload.get("created_at_iso") or ""),
            status=str(payload.get("status") or "scheduled"),
            retries=int(payload.get("retries") or 0),
            fired_at_iso=str(payload.get("fired_at_iso") or ""),
            last_error=str(payload.get("last_error") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reminder_id": self.reminder_id,
            "chat_id": self.chat_id,
            "text": self.text,
            "due_at_iso": self.due_at_iso,
            "created_at_iso": self.created_at_iso,
            "status": self.status,
            "retries": self.retries,
            "fired_at_iso": self.fired_at_iso,
            "last_error": self.last_error,
        }


class KrabScheduler:
    """
    Минимальный runtime scheduler.

    Поддерживает:
    - one-shot задачи (`add_once_task`);
    - reminders с persistence/retry (`add_reminder`).
    """

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or (
            config.BASE_DIR / "data" / "scheduler" / "reminders.json"
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = False
        self._jobs: dict[str, asyncio.Task] = {}
        self._reminders: dict[str, ReminderRecord] = {}
        self._sender: Callable[[str, str], Awaitable[None]] | None = None
        self._max_retries = 5
        self._retry_delay_sec = 60.0
        # chat_id владельца для перенаправления напоминаний из групп
        self._owner_chat_id: str = ""

    @property
    def is_started(self) -> bool:
        return self._started

    def bind_sender(self, sender: Callable[[str, str], Awaitable[None]]) -> None:
        """Привязывает async callback для отправки scheduled сообщений в канал."""
        self._sender = sender

    def bind_owner_chat_id(self, owner_chat_id: str) -> None:
        """Привязывает chat_id владельца для перенаправления напоминаний из групп."""
        self._owner_chat_id = str(owner_chat_id)

    def start(self) -> None:
        """Старт scheduler в текущем event loop."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        self._started = True
        self._load()
        self._reschedule_pending()
        logger.info("scheduler_started", reminders=len(self._reminders))

    def stop(self) -> None:
        """Остановка scheduler и отмена pending задач."""
        for task in list(self._jobs.values()):
            if not task.done():
                task.cancel()
        self._jobs.clear()
        self._started = False
        self._persist()
        logger.info("scheduler_stopped", reminders=len(self._reminders))

    def add_once_task(self, callback: Callable[[], Any], *, delay_seconds: float) -> str:
        """Планирует одноразовую задачу через delay."""
        if not self._started or not self._loop:
            raise RuntimeError("scheduler_not_started")
        job_id = f"once:{uuid.uuid4().hex}"

        async def _runner() -> None:
            try:
                await asyncio.sleep(max(0.0, float(delay_seconds)))
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("scheduler_once_task_failed", job_id=job_id, error=str(exc))
            finally:
                self._jobs.pop(job_id, None)

        self._jobs[job_id] = self._loop.create_task(_runner())
        return job_id

    def add_reminder(self, *, chat_id: str, text: str, due_at: datetime) -> str:
        """Создает reminder и ставит его в планировщик."""
        if not self._started:
            raise RuntimeError("scheduler_not_started")
        reminder_id = uuid.uuid4().hex[:12]
        record = ReminderRecord(
            reminder_id=reminder_id,
            chat_id=str(chat_id),
            text=str(text or "").strip(),
            due_at_iso=due_at.astimezone().isoformat(),
            created_at_iso=_now_local().isoformat(),
            status="scheduled",
            retries=0,
        )
        self._reminders[reminder_id] = record
        self._persist()
        inbox_service.upsert_reminder(
            reminder_id=record.reminder_id,
            chat_id=record.chat_id,
            text=record.text,
            due_at_iso=record.due_at_iso,
            retries=record.retries,
            last_error=record.last_error,
        )
        self._schedule_reminder(reminder_id)
        return reminder_id

    def remove_reminder(self, reminder_id: str) -> bool:
        """Удаляет reminder (если есть)."""
        rid = str(reminder_id or "").strip()
        if not rid:
            return False
        record = self._reminders.get(rid)
        if not record:
            return False
        record.status = "cancelled"
        task = self._jobs.pop(rid, None)
        if task and not task.done():
            task.cancel()
        self._reminders.pop(rid, None)
        self._persist()
        inbox_service.resolve_reminder(rid, status="cancelled")
        return True

    def list_reminders(self, *, chat_id: str | None = None) -> list[dict[str, Any]]:
        """Возвращает pending reminders (опционально по chat_id)."""
        target_chat = str(chat_id).strip() if chat_id is not None else ""
        rows: list[ReminderRecord] = []
        for rec in self._reminders.values():
            if rec.status != "scheduled":
                continue
            if target_chat and rec.chat_id != target_chat:
                continue
            rows.append(rec)
        rows.sort(key=lambda item: item.due_at_iso)
        return [r.to_dict() for r in rows]

    def get_status(self) -> dict[str, Any]:
        """Диагностический срез для `!cronstatus`."""
        pending = self.list_reminders()
        next_due = pending[0]["due_at_iso"] if pending else ""
        return {
            "started": self._started,
            "pending_count": len(pending),
            "next_due_at": next_due,
            "scheduler_enabled": bool(getattr(config, "SCHEDULER_ENABLED", False)),
            "storage_path": str(self.storage_path),
        }

    def _reschedule_pending(self) -> None:
        for reminder_id, rec in list(self._reminders.items()):
            if rec.status != "scheduled":
                continue
            self._schedule_reminder(reminder_id)

    def _schedule_reminder(self, reminder_id: str) -> None:
        if not self._started or not self._loop:
            return
        rec = self._reminders.get(reminder_id)
        if not rec or rec.status != "scheduled":
            return
        try:
            due_at = datetime.fromisoformat(rec.due_at_iso).astimezone()
        except Exception:  # noqa: BLE001
            rec.status = "failed"
            rec.last_error = "bad_due_at_iso"
            self._persist()
            return
        delay = max(0.0, (due_at - _now_local()).total_seconds())
        existing = self._jobs.get(reminder_id)
        if existing and not existing.done():
            existing.cancel()
        self._jobs[reminder_id] = self._loop.create_task(self._run_reminder(reminder_id, delay))

    async def _run_reminder(self, reminder_id: str, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._fire_reminder(reminder_id)
        except asyncio.CancelledError:
            return
        finally:
            self._jobs.pop(reminder_id, None)

    async def _fire_reminder(self, reminder_id: str) -> None:
        rec = self._reminders.get(reminder_id)
        if not rec or rec.status != "scheduled":
            return

        # Если напоминание создано в группе (отрицательный chat_id) — перенаправляем в DM владельца
        target_chat_id = rec.chat_id
        group_note = ""
        if rec.chat_id.lstrip("-").isdigit() and rec.chat_id.startswith("-"):
            if self._owner_chat_id:
                group_note = f"\n_(создано в чате {rec.chat_id})_"
                target_chat_id = self._owner_chat_id
                logger.info(
                    "reminder_redirected_to_owner_dm",
                    reminder_id=reminder_id,
                    original_chat=rec.chat_id,
                    owner_chat=self._owner_chat_id,
                )

        payload = f"⏰ Напоминание\n\n{rec.text}{group_note}"
        sender = self._sender
        if sender is None:
            await self._retry_or_fail(rec, "sender_not_bound")
            return
        try:
            await sender(target_chat_id, payload)
            rec.status = "done"
            rec.fired_at_iso = _now_local().isoformat()
            rec.last_error = ""
            self._reminders.pop(reminder_id, None)
            self._persist()
            inbox_service.upsert_item(
                dedupe_key=f"proactive:reminder_fired:{reminder_id}",
                kind="proactive_action",
                source="krab-internal",
                title=f"Reminder delivered: {rec.text[:80]}",
                body=(f"Chat: `{rec.chat_id}`\nText: {rec.text}\nFired at: `{rec.fired_at_iso}`"),
                severity="info",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id=str(rec.chat_id),
                    team_id="owner",
                    trace_id=reminder_id,
                    approval_scope="owner",
                ),
                metadata={
                    "action_type": "reminder_fired",
                    "reminder_id": reminder_id,
                    "chat_id": rec.chat_id,
                },
            )
            inbox_service.resolve_reminder(reminder_id, status="done")
        except Exception as exc:  # noqa: BLE001
            await self._retry_or_fail(rec, f"send_error:{exc}")

    async def _retry_or_fail(self, rec: ReminderRecord, reason: str) -> None:
        # Increment retry count and record last_error
        rec.retries += 1
        rec.last_error = reason

        # If retries > max_retries (5), set status to "failed" and create warning inbox item
        if rec.retries > self._max_retries:
            rec.status = "failed"
            self._persist()

            # Create warning inbox item for failed reminder
            try:
                inbox_service.upsert_item(
                    dedupe_key=f"reminder_failed:{rec.reminder_id}",
                    kind="reminder_failure",
                    source="scheduler",
                    title=f"Reminder failed after {rec.retries} attempts",
                    body=(
                        f"Chat: `{rec.chat_id}`\n"
                        f"Text: {rec.text}\n"
                        f"Last error: `{reason}`\n"
                        f"Retries: `{rec.retries}`"
                    ),
                    severity="warning",
                    status="open",
                    identity=inbox_service.build_identity(
                        channel_id=str(rec.chat_id),
                        team_id="owner",
                        trace_id=rec.reminder_id,
                        approval_scope="owner",
                    ),
                    metadata={
                        "reminder_id": rec.reminder_id,
                        "chat_id": rec.chat_id,
                        "retries": rec.retries,
                        "last_error": reason,
                        "failed_at": _now_local().isoformat(),
                    },
                )
            except Exception as inbox_exc:  # noqa: BLE001
                logger.warning(
                    "scheduler_failed_reminder_inbox_sync_failed",
                    reminder_id=rec.reminder_id,
                    error=str(inbox_exc),
                )

            # Update inbox reminder with failure status
            try:
                inbox_service.upsert_reminder(
                    reminder_id=rec.reminder_id,
                    chat_id=rec.chat_id,
                    text=rec.text,
                    due_at_iso=rec.due_at_iso,
                    retries=rec.retries,
                    last_error=reason,
                )
            except Exception as sync_exc:  # noqa: BLE001
                logger.warning(
                    "scheduler_failed_reminder_sync_failed",
                    reminder_id=rec.reminder_id,
                    error=str(sync_exc),
                )

            logger.warning("scheduler_reminder_failed", reminder_id=rec.reminder_id, reason=reason)
            return

        # Otherwise, reschedule with 60 second delay
        next_due = _now_local() + timedelta(seconds=self._retry_delay_sec)
        rec.due_at_iso = next_due.isoformat()
        rec.status = "scheduled"
        self._persist()

        # Sync to inbox with retry count and last_error after each attempt
        try:
            inbox_service.upsert_reminder(
                reminder_id=rec.reminder_id,
                chat_id=rec.chat_id,
                text=rec.text,
                due_at_iso=rec.due_at_iso,
                retries=rec.retries,
                last_error=reason,
            )
        except Exception as sync_exc:  # noqa: BLE001
            logger.warning(
                "scheduler_retry_inbox_sync_failed",
                reminder_id=rec.reminder_id,
                error=str(sync_exc),
            )

        self._schedule_reminder(rec.reminder_id)
        logger.warning(
            "scheduler_reminder_retry",
            reminder_id=rec.reminder_id,
            retry=rec.retries,
            reason=reason,
            next_due=rec.due_at_iso,
        )

    def _load(self) -> None:
        """Загружает reminders из persisted storage с graceful recovery."""
        self._reminders.clear()

        # Если файл не существует, инициализируем пустое состояние
        if not self.storage_path.exists():
            logger.info("scheduler_load_empty", path=str(self.storage_path))
            return

        loaded_count = 0
        failed_parsing = 0
        invalid_records = 0

        try:
            # Пытаемся прочитать и распарсить JSON
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("scheduler_load_failed", path=str(self.storage_path), error=str(exc))
            # Инициализируем пустое состояние при ошибке парсинга
            logger.warning(
                "scheduler_load_corrupted",
                path=str(self.storage_path),
                action="initializing_empty_state",
            )
            return

        # Извлекаем reminders из payload
        rows = payload.get("reminders", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list):
            rows = []

        for idx, item in enumerate(rows):
            if not isinstance(item, dict):
                invalid_records += 1
                logger.warning("scheduler_load_invalid_row", index=idx, type=type(item).__name__)
                continue

            try:
                # Пытаемся создать ReminderRecord
                rec = ReminderRecord.from_dict(item)

                # Валидируем обязательные поля
                if not rec.reminder_id or not rec.chat_id or not rec.text or not rec.due_at_iso:
                    logger.warning(
                        "scheduler_load_invalid_record",
                        reminder_id=rec.reminder_id or "unknown",
                        missing_fields=", ".join(
                            [
                                "reminder_id" if not rec.reminder_id else "",
                                "chat_id" if not rec.chat_id else "",
                                "text" if not rec.text else "",
                                "due_at_iso" if not rec.due_at_iso else "",
                            ]
                        ).strip(", "),
                    )
                    invalid_records += 1
                    continue

                # Валидируем due_at_iso
                try:
                    due_datetime = datetime.fromisoformat(rec.due_at_iso.replace("Z", "+00:00"))
                    # Если дата в прошлом, помечаем как failed
                    if due_datetime < datetime.now(due_datetime.tzinfo):
                        rec.status = "failed"
                        rec.last_error = "due_date_in_past"
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "scheduler_load_invalid_date",
                        reminder_id=rec.reminder_id,
                        due_at_iso=rec.due_at_iso,
                        error=str(exc),
                    )
                    rec.status = "failed"
                    rec.last_error = f"invalid_due_at_format: {exc}"

                # Добавляем в коллекцию
                self._reminders[rec.reminder_id] = rec
                loaded_count += 1

                # Синхронизируем с InboxService
                try:
                    inbox_service.upsert_reminder(
                        reminder_id=rec.reminder_id,
                        chat_id=rec.chat_id,
                        text=rec.text,
                        due_at_iso=rec.due_at_iso,
                        retries=rec.retries,
                        last_error=rec.last_error,
                    )
                except Exception as sync_exc:
                    logger.warning(
                        "scheduler_load_inbox_sync_failed",
                        reminder_id=rec.reminder_id,
                        error=str(sync_exc),
                    )

            except Exception as exc:  # noqa: BLE001
                failed_parsing += 1
                logger.warning("scheduler_load_record_failed", index=idx, error=str(exc))
                continue

        # Логируем результаты загрузки
        logger.info(
            "scheduler_load_complete",
            loaded=loaded_count,
            invalid=invalid_records,
            failed_parsing=failed_parsing,
            total_loaded=len(self._reminders),
        )

        # Синхронизируем все загруженные reminders с InboxService
        for rec in self._reminders.values():
            if rec.status == "scheduled":
                try:
                    inbox_service.upsert_reminder(
                        reminder_id=rec.reminder_id,
                        chat_id=rec.chat_id,
                        text=rec.text,
                        due_at_iso=rec.due_at_iso,
                        retries=rec.retries,
                        last_error=rec.last_error,
                    )
                except Exception as sync_exc:  # noqa: BLE001
                    logger.warning(
                        "scheduler_load_inbox_sync_failed",
                        reminder_id=rec.reminder_id,
                        error=str(sync_exc),
                    )

    def _persist(self) -> None:
        try:
            # Создаем parent directories если отсутствуют
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)

            rows = [rec.to_dict() for rec in self._reminders.values() if rec.status == "scheduled"]
            payload = {
                "updated_at": _now_local().isoformat(),
                "reminders": rows,
            }

            # Atomic write pattern: write to temp file, then rename
            temp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
            try:
                temp_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                # Atomic rename
                temp_path.replace(self.storage_path)
            except Exception:
                # Cleanup temp file if something went wrong
                if temp_path.exists():
                    temp_path.unlink(missing_ok=True)
                raise

            # Sync reminder state to InboxService after each persist
            for rec in self._reminders.values():
                if rec.status == "scheduled":
                    try:
                        inbox_service.upsert_reminder(
                            reminder_id=rec.reminder_id,
                            chat_id=rec.chat_id,
                            text=rec.text,
                            due_at_iso=rec.due_at_iso,
                            retries=rec.retries,
                            last_error=rec.last_error,
                        )
                    except Exception as sync_exc:  # noqa: BLE001
                        logger.warning(
                            "scheduler_inbox_sync_failed",
                            reminder_id=rec.reminder_id,
                            error=str(sync_exc),
                        )

        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduler_persist_failed", path=str(self.storage_path), error=str(exc))


krab_scheduler = KrabScheduler()
