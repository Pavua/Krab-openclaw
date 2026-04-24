"""Native Python cron job store — fallback когда OpenClaw CLI недоступен.

Job file: ~/.openclaw/krab_runtime_state/cron_native_jobs.json
Schema: {"version": 1, "jobs": [{"id", "cron_spec", "prompt", "enabled", "created_at"}]}
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

# Путь к файлу хранения (переопределяется через configure_default_path)
_DEFAULT_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "cron_native_jobs.json"
_storage_path: Path = _DEFAULT_PATH


def configure_default_path(path: Path) -> None:
    """Переключает путь хранилища (для тестов и bootstrap)."""
    global _storage_path
    _storage_path = path


def _load() -> list[dict]:
    """Читает список jobs из файла, возвращает пустой список при ошибке."""
    # Missing file — нормальное состояние bootstrap'а, не логируем как warning
    # (раньше это давало 1230+ warnings/сессию — чистый шум в логах).
    if not _storage_path.exists():
        return []
    try:
        data = json.loads(_storage_path.read_text(encoding="utf-8", errors="replace"))
        return list(data.get("jobs", [])) if isinstance(data, dict) else []
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "cron_native_store_load_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            path=str(_storage_path),
        )
        return []


def _save(jobs: list[dict]) -> None:
    """Сохраняет список jobs в файл (создаёт директорию при необходимости)."""
    _storage_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"version": 1, "jobs": jobs}
    _storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_jobs() -> list[dict]:
    """Возвращает копию списка всех native cron jobs."""
    return list(_load())


def add_job(cron_spec: str, prompt: str, job_id: str | None = None) -> str:
    """Создаёт новый job, возвращает его id."""
    jobs = _load()
    new_id = job_id or str(uuid.uuid4())[:8]
    job: dict[str, Any] = {
        "id": new_id,
        "cron_spec": cron_spec,
        "prompt": prompt,
        "enabled": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run_at": None,
        "run_count": 0,
    }
    jobs.append(job)
    _save(jobs)
    logger.info("cron_native_job_added", job_id=new_id, cron_spec=cron_spec)
    return new_id


def remove_job(job_id: str) -> bool:
    """Удаляет job по id. Возвращает True если job был найден и удалён."""
    jobs = _load()
    before = len(jobs)
    jobs = [j for j in jobs if j.get("id") != job_id]
    if len(jobs) == before:
        return False
    _save(jobs)
    logger.info("cron_native_job_removed", job_id=job_id)
    return True


def toggle_job(job_id: str, enabled: bool) -> bool:
    """Включает/выключает job. Возвращает True если job найден."""
    jobs = _load()
    found = False
    for j in jobs:
        if j.get("id") == job_id:
            j["enabled"] = enabled
            found = True
            break
    if not found:
        return False
    _save(jobs)
    logger.info("cron_native_job_toggled", job_id=job_id, enabled=enabled)
    return True


def mark_run(job_id: str) -> None:
    """Обновляет last_run_at и run_count после выполнения job."""
    jobs = _load()
    for j in jobs:
        if j.get("id") == job_id:
            j["last_run_at"] = datetime.now(timezone.utc).isoformat()
            j["run_count"] = int(j.get("run_count") or 0) + 1
            break
    _save(jobs)


class _CronDatetime(datetime):
    """datetime с методом weekday_cron() — воскресенье = 0 (unix cron)."""

    def weekday_cron(self) -> int:
        # Python weekday(): Mon=0..Sun=6 → cron: Sun=0..Sat=6
        wd = self.weekday()
        return (wd + 1) % 7


def _field_match(field: str, value: int, lo: int, hi: int) -> bool:  # noqa: ARG001
    """Проверяет соответствие value cron-полю (*, */N, N, N-M)."""
    if field == "*":
        return True
    if field.startswith("*/"):
        try:
            step = int(field[2:])
            return step > 0 and (value - lo) % step == 0
        except ValueError:
            return False
    if "-" in field:
        try:
            a, b = field.split("-", 1)
            return int(a) <= value <= int(b)
        except ValueError:
            return False
    try:
        return int(field) == value
    except ValueError:
        return False


def next_due(job: dict, now: datetime | None = None) -> float | None:
    """
    Вычисляет timestamp (UTC) следующего срабатывания job.

    Использует стандартный cron (5 полей: M H D Mo Dow).
    Возвращает None если cron_spec некорректен.
    """
    cron_spec = str(job.get("cron_spec") or "").strip()
    if not cron_spec:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    parts = cron_spec.strip().split()
    if len(parts) != 5:
        return None
    minute_f, hour_f, day_f, month_f, dow_f = parts

    # Начинаем со следующей минуты
    base = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    candidate = _CronDatetime(
        base.year,
        base.month,
        base.day,
        base.hour,
        base.minute,
        0,
        0,
        tzinfo=base.tzinfo,
    )
    limit = now + timedelta(days=366)

    while candidate < limit:
        if (
            _field_match(minute_f, candidate.minute, 0, 59)
            and _field_match(hour_f, candidate.hour, 0, 23)
            and _field_match(day_f, candidate.day, 1, 31)
            and _field_match(month_f, candidate.month, 1, 12)
            and _field_match(dow_f, candidate.weekday_cron(), 0, 6)
        ):
            return candidate.timestamp()
        nxt = candidate + timedelta(minutes=1)
        candidate = _CronDatetime(
            nxt.year,
            nxt.month,
            nxt.day,
            nxt.hour,
            nxt.minute,
            0,
            0,
            tzinfo=nxt.tzinfo,
        )

    logger.warning(
        "cron_native_next_due_not_found",
        cron_spec=cron_spec,
        now=now.isoformat(),
    )
    return None
