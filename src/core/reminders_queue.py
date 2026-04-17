# -*- coding: utf-8 -*-
"""
Reminders Queue — proactivity level 2.

Time-based: "через 2 часа проверь X" → запускает action в delta_time.
Event-based: "когда в чате N появится тема Y сделай Z" → trigger on match.

Persistence: JSON-файл в ~/.openclaw/krab_runtime_state/reminders_queue.json
Trigger: background asyncio task, проверяет каждые 30 сек.

TODO Session 11.1: wire reminders_queue into userbot_bridge._process_message
(check_event_match call after message receipt) + start_loop in startup.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .logger import get_logger

logger = get_logger(__name__)

# Путь к файлу состояния (переопределяется в тестах)
STATE_PATH = Path("~/.openclaw/krab_runtime_state/reminders_queue.json").expanduser()
CHECK_INTERVAL_SEC = 30


class ReminderTrigger(str, Enum):
    """Тип триггера напоминания."""

    TIME = "time"  # срабатывает в конкретный timestamp
    EVENT = "event"  # срабатывает при совпадении условия


class ReminderStatus(str, Enum):
    """Статус напоминания."""

    PENDING = "pending"
    FIRED = "fired"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Reminder:
    """Запись о напоминании."""

    id: str  # uuid hex (первые 12 символов)
    owner_user_id: str
    created_at: int  # unix seconds
    trigger_type: ReminderTrigger
    # Time-based поля:
    fire_at: Optional[int] = None  # unix timestamp когда сработать
    # Event-based поля:
    watch_chat_id: Optional[str] = None
    match_pattern: Optional[str] = None  # regex для message text
    # Action:
    action_type: str = "notify"  # "notify" | "ai_query" | "command"
    action_payload: str = ""  # текст, запрос или команда
    # State:
    status: ReminderStatus = ReminderStatus.PENDING
    fired_at: Optional[int] = None
    last_error: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Reminder":
        """Десериализация из словаря (с приведением enum-полей)."""
        kwargs = dict(data)
        trigger_raw = kwargs.get("trigger_type")
        if isinstance(trigger_raw, str):
            kwargs["trigger_type"] = ReminderTrigger(trigger_raw)
        status_raw = kwargs.get("status")
        if isinstance(status_raw, str):
            kwargs["status"] = ReminderStatus(status_raw)
        return cls(**kwargs)


# Callback подпись: принимает Reminder, возвращает awaitable
FireCallback = Callable[[Reminder], Awaitable[None]]


class RemindersQueue:
    """Очередь напоминаний (time-based + event-based)."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or STATE_PATH
        self._reminders: dict[str, Reminder] = {}
        self._callback: Optional[FireCallback] = None
        self._task: Optional[asyncio.Task] = None
        self._load()

    # ─── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        """Читает напоминания с диска. Fail-safe — при ошибке логируем и продолжаем."""
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            for item in data.get("reminders", []):
                r = Reminder.from_dict(item)
                self._reminders[r.id] = r
        except Exception as e:  # noqa: BLE001 — хотим проглотить ЛЮБУЮ ошибку загрузки
            logger.warning("reminders_load_failed", error=str(e))

    def _save(self) -> None:
        """Сохраняет текущее состояние на диск."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"reminders": [asdict(r) for r in self._reminders.values()]}
        self._state_path.write_text(
            json.dumps(payload, default=str, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ─── Mutations ────────────────────────────────────────────────────────

    def add_time_reminder(
        self,
        owner_id: str,
        fire_at: int,
        action: str,
        action_type: str = "notify",
    ) -> str:
        """Добавить time-based напоминание. Возвращает id."""
        r = Reminder(
            id=uuid.uuid4().hex[:12],
            owner_user_id=owner_id,
            created_at=int(time.time()),
            trigger_type=ReminderTrigger.TIME,
            fire_at=fire_at,
            action_type=action_type,
            action_payload=action,
        )
        self._reminders[r.id] = r
        self._save()
        return r.id

    def add_event_reminder(
        self,
        owner_id: str,
        chat_id: str,
        pattern: str,
        action: str,
    ) -> str:
        """Добавить event-based напоминание (regex по тексту сообщения)."""
        r = Reminder(
            id=uuid.uuid4().hex[:12],
            owner_user_id=owner_id,
            created_at=int(time.time()),
            trigger_type=ReminderTrigger.EVENT,
            watch_chat_id=str(chat_id),
            match_pattern=pattern,
            action_type="notify",
            action_payload=action,
        )
        self._reminders[r.id] = r
        self._save()
        return r.id

    def cancel(self, reminder_id: str) -> bool:
        """Отменить pending reminder. Возвращает True если отменили."""
        r = self._reminders.get(reminder_id)
        if r is None or r.status != ReminderStatus.PENDING:
            return False
        r.status = ReminderStatus.CANCELLED
        self._save()
        return True

    def list_pending(self, owner_id: Optional[str] = None) -> list[Reminder]:
        """Список pending напоминаний (опционально — конкретного owner)."""
        result = [r for r in self._reminders.values() if r.status == ReminderStatus.PENDING]
        if owner_id is not None:
            result = [r for r in result if r.owner_user_id == owner_id]
        return result

    def get(self, reminder_id: str) -> Optional[Reminder]:
        """Получить reminder по id."""
        return self._reminders.get(reminder_id)

    def set_fire_callback(self, cb: FireCallback) -> None:
        """Установить callback для срабатывания напоминаний.

        cb: async (reminder: Reminder) -> None
        """
        self._callback = cb

    # ─── Triggering ───────────────────────────────────────────────────────

    async def check_time_reminders(self) -> list[str]:
        """Проверка time-based напоминаний. Возвращает id-шники сработавших."""
        now = int(time.time())
        fired: list[str] = []
        changed = False
        for r in list(self._reminders.values()):
            if r.status != ReminderStatus.PENDING or r.trigger_type != ReminderTrigger.TIME:
                continue
            if r.fire_at is None or now < r.fire_at:
                continue
            try:
                if self._callback is not None:
                    await self._callback(r)
                r.status = ReminderStatus.FIRED
                r.fired_at = now
                fired.append(r.id)
                changed = True
            except Exception as e:  # noqa: BLE001 — изолируем падение callback от loop
                r.status = ReminderStatus.FAILED
                r.last_error = str(e)
                changed = True
                logger.error("reminder_fire_failed", id=r.id, error=str(e))
        if changed:
            self._save()
        return fired

    def check_event_match(self, chat_id: str, message_text: str) -> list[Reminder]:
        """Вернуть pending event-reminders, которые матчатся на сообщение."""
        matched: list[Reminder] = []
        for r in self._reminders.values():
            if r.status != ReminderStatus.PENDING or r.trigger_type != ReminderTrigger.EVENT:
                continue
            if str(r.watch_chat_id) != str(chat_id):
                continue
            if not r.match_pattern:
                continue
            try:
                if re.search(r.match_pattern, message_text, re.IGNORECASE):
                    matched.append(r)
            except re.error:
                # Битый regex — молча пропускаем
                continue
        return matched

    async def fire_event_reminder(self, reminder: Reminder) -> None:
        """Запустить event-reminder (вызывается после check_event_match)."""
        try:
            if self._callback is not None:
                await self._callback(reminder)
            reminder.status = ReminderStatus.FIRED
            reminder.fired_at = int(time.time())
        except Exception as e:  # noqa: BLE001
            reminder.status = ReminderStatus.FAILED
            reminder.last_error = str(e)
            logger.error("event_reminder_fire_failed", id=reminder.id, error=str(e))
        finally:
            self._save()

    async def start_loop(self) -> None:
        """Фоновый loop для time-based reminders (проверка каждые CHECK_INTERVAL_SEC)."""
        while True:
            try:
                await self.check_time_reminders()
            except Exception as e:  # noqa: BLE001
                logger.error("reminders_loop_error", error=str(e))
            await asyncio.sleep(CHECK_INTERVAL_SEC)


# Singleton для использования по всему runtime
reminders_queue = RemindersQueue()
