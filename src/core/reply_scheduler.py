# -*- coding: utf-8 -*-
"""
ReplyScheduler — отложенная отправка ответов через function-call LLM.

LLM может вызвать `schedule_reply(chat_id, text, send_at_iso)`, и сообщение будет
доставлено в указанное время. Сам отправитель (pyrogram-tick) выносится в bridge —
этот модуль отвечает только за персистентное хранилище и атомарный pop_due().

Хранилище: ~/.openclaw/krab_runtime_state/scheduled_replies.json
Структура записи: {job_id, chat_id, text, send_at, created_at, owner_id}

Idea 5 — Reply scheduling tool.
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .logger import get_logger

logger = get_logger(__name__)

# Дефолтный путь хранилища
_DEFAULT_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "scheduled_replies.json"


@dataclass
class ScheduledReply:
    """Запись об отложенном ответе."""

    job_id: str
    chat_id: int
    text: str
    send_at: datetime  # timezone-aware
    created_at: datetime
    owner_id: int | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Сериализация в JSON-совместимый dict (datetime → ISO 8601)."""
        data = asdict(self)
        data["send_at"] = self.send_at.isoformat()
        data["created_at"] = self.created_at.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ScheduledReply:
        """Десериализация из dict (ISO 8601 → datetime)."""
        return cls(
            job_id=data["job_id"],
            chat_id=int(data["chat_id"]),
            text=data["text"],
            send_at=_parse_iso(data["send_at"]),
            created_at=_parse_iso(data["created_at"]),
            owner_id=data.get("owner_id"),
            metadata=data.get("metadata", {}) or {},
        )


def _parse_iso(value: str | datetime) -> datetime:
    """Парсит ISO-строку или возвращает datetime as-is. Naive → UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        # fromisoformat в Python 3.11+ понимает 'Z' нативно
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class ReplyScheduler:
    """Singleton-планировщик отложенных ответов с JSON-persistence."""

    def __init__(
        self,
        storage_path: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        # Путь хранилища (для тестов можно подменить)
        self._path: Path = storage_path or _DEFAULT_PATH
        # Источник текущего времени (для тестов — fake clock)
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(timezone.utc))
        # Lazy-load: None == ещё не читали
        self._jobs: list[ScheduledReply] | None = None
        # Защита от гонок при concurrent pop_due/schedule
        self._lock = threading.RLock()

    # ─── Конфигурация ────────────────────────────────────────────────────────

    def configure_default_path(self, path: Path) -> None:
        """Перенацеливает singleton на актуальный runtime_state_dir (bootstrap)."""
        with self._lock:
            self._path = path
            self._jobs = None  # принудительная перезагрузка

    # ─── Persistence ─────────────────────────────────────────────────────────

    def _load_from_disk(self) -> list[ScheduledReply]:
        """Лениво подгружает jobs из файла. Возвращает копию состояния."""
        if self._jobs is not None:
            return self._jobs
        if not self._path.exists():
            self._jobs = []
            return self._jobs
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("scheduled_replies.json must be a list")
            self._jobs = [ScheduledReply.from_dict(item) for item in raw]
        except (json.JSONDecodeError, OSError, ValueError, KeyError) as exc:
            logger.warning(
                "scheduled_replies_load_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )
            self._jobs = []
        return self._jobs

    def _persist(self) -> None:
        """Сохраняет текущее состояние jobs на диск."""
        if self._jobs is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = [j.to_dict() for j in self._jobs]
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error(
                "scheduled_replies_persist_failed",
                extra={"error": str(exc), "error_type": type(exc).__name__},
            )

    # ─── Публичный API ───────────────────────────────────────────────────────

    def schedule(
        self,
        chat_id: int,
        text: str,
        send_at: datetime | str,
        *,
        owner_id: int | None = None,
        metadata: dict | None = None,
    ) -> str:
        """
        Регистрирует отложенный ответ.

        Args:
            chat_id: целевой Telegram chat_id
            text: текст сообщения
            send_at: момент отправки (datetime tz-aware либо ISO 8601-строка)
            owner_id: id оператора, поставившего задачу (для multi-owner изоляции)
            metadata: произвольные доп. данные (reply_to_message_id и т.д.)

        Returns:
            job_id — идентификатор записи (для cancel/list).
        """
        if not text or not text.strip():
            raise ValueError("schedule_reply: text must be non-empty")

        send_at_dt = _parse_iso(send_at) if isinstance(send_at, str) else send_at
        if send_at_dt.tzinfo is None:
            send_at_dt = send_at_dt.replace(tzinfo=timezone.utc)

        with self._lock:
            jobs = self._load_from_disk()
            job_id = secrets.token_hex(8)
            entry = ScheduledReply(
                job_id=job_id,
                chat_id=int(chat_id),
                text=text,
                send_at=send_at_dt,
                created_at=self._now(),
                owner_id=owner_id,
                metadata=dict(metadata or {}),
            )
            jobs.append(entry)
            self._persist()

        logger.info(
            "reply_scheduler_job_added",
            extra={
                "job_id": job_id,
                "chat_id": chat_id,
                "send_at": send_at_dt.isoformat(),
                "owner_id": owner_id,
            },
        )
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Удаляет задачу по job_id. True — удалена, False — не найдена."""
        with self._lock:
            jobs = self._load_from_disk()
            before = len(jobs)
            self._jobs = [j for j in jobs if j.job_id != job_id]
            if len(self._jobs) == before:
                return False
            self._persist()
        logger.info("reply_scheduler_job_cancelled", extra={"job_id": job_id})
        return True

    def list_pending(self, *, owner_id: int | None = None) -> list[ScheduledReply]:
        """
        Возвращает копии pending-записей (отсортированные по send_at).

        Если owner_id указан — фильтр по нему (multi-owner isolation).
        """
        with self._lock:
            jobs = list(self._load_from_disk())
        if owner_id is not None:
            jobs = [j for j in jobs if j.owner_id == owner_id]
        return sorted(jobs, key=lambda j: j.send_at)

    def pop_due(self, now: datetime | None = None) -> list[ScheduledReply]:
        """
        Атомарно вынимает все задачи с send_at <= now.

        Используется тиком фоновой петли в bridge.
        """
        cutoff = now or self._now()
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

        with self._lock:
            jobs = self._load_from_disk()
            due = [j for j in jobs if j.send_at <= cutoff]
            if not due:
                return []
            due_ids = {j.job_id for j in due}
            self._jobs = [j for j in jobs if j.job_id not in due_ids]
            self._persist()

        logger.info("reply_scheduler_pop_due", extra={"count": len(due)})
        return due


# Singleton
reply_scheduler = ReplyScheduler()
