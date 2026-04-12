# -*- coding: utf-8 -*-
"""
src/core/swarm_scheduler.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Рекуррентный планировщик автономных прогонов свёрма.

Зачем:
- `!swarm schedule traders "BTC анализ" 4h` → автоматический запуск каждые 4 часа
- результаты сохраняются в swarm_memory и отправляются owner-у
- гейт SWARM_AUTONOMOUS_ENABLED (дефолт: false)

Связь с проектом:
- использует scheduler.py через add_once_task для отложенных запусков;
- использует swarm.py / swarm_bus.py для прогонов;
- использует swarm_memory.py для сохранения результатов;
- persisted state: ~/.openclaw/krab_runtime_state/swarm_recurring_jobs.json
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import config
from .logger import get_logger

logger = get_logger(__name__)

_STATE_PATH = Path.home() / ".openclaw" / "krab_runtime_state" / "swarm_recurring_jobs.json"

# Минимальный интервал — 5 минут (защита от случайного спама)
_MIN_INTERVAL_SEC = 300

# Максимум рекуррентных jobs
_MAX_JOBS = 10

class WorkflowType(str, Enum):
    """Тип workflow для рекуррентного job."""

    STANDARD = "standard"   # обычный round свёрма
    RESEARCH = "research"   # research pipeline (web_search обязателен)
    REPORT = "report"       # генерация отчёта и сохранение артефакта


_INTERVAL_PATTERN_MAP = {
    "m": 60,
    "min": 60,
    "мин": 60,
    "h": 3600,
    "ч": 3600,
    "час": 3600,
    "d": 86400,
    "д": 86400,
    "дн": 86400,
}


def parse_interval(spec: str) -> int:
    """
    Парсит интервал вида '4h', '30m', '1d', '2ч', '30мин'.
    Возвращает секунды.
    """
    import re

    spec = spec.strip().lower()
    m = re.match(r"^(\d+)\s*([a-zа-яё]+)$", spec)
    if not m:
        raise ValueError(f"Непонятный интервал: {spec}")
    amount = int(m.group(1))
    unit = m.group(2)
    for suffix, scale in _INTERVAL_PATTERN_MAP.items():
        if unit.startswith(suffix):
            seconds = amount * scale
            if seconds < _MIN_INTERVAL_SEC:
                raise ValueError(f"Минимальный интервал: {_MIN_INTERVAL_SEC // 60} мин")
            return seconds
    raise ValueError(f"Неизвестная единица: {unit}")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class RecurringJob:
    """Запись рекуррентной задачи свёрма."""

    job_id: str
    team: str
    topic: str
    interval_sec: int
    workflow_type: str = WorkflowType.STANDARD  # "standard" | "research" | "report"
    created_at: str = field(default_factory=_now_utc_iso)
    last_run_at: str = ""
    next_run_at: str = ""
    total_runs: int = 0
    last_error: str = ""
    enabled: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RecurringJob:
        # Поддержка старых записей без workflow_type — дефолт standard
        raw_wf = d.get("workflow_type", WorkflowType.STANDARD)
        try:
            wf = WorkflowType(raw_wf).value
        except ValueError:
            wf = WorkflowType.STANDARD.value
        return cls(
            job_id=d.get("job_id", ""),
            team=d.get("team", ""),
            topic=d.get("topic", ""),
            interval_sec=int(d.get("interval_sec", 0)),
            workflow_type=wf,
            created_at=d.get("created_at", ""),
            last_run_at=d.get("last_run_at", ""),
            next_run_at=d.get("next_run_at", ""),
            total_runs=int(d.get("total_runs", 0)),
            last_error=d.get("last_error", ""),
            enabled=bool(d.get("enabled", True)),
        )


class SwarmScheduler:
    """
    Рекуррентный планировщик автономных свёрм-задач.

    Каждый job запускает AgentRoom.run_round() по расписанию,
    сохраняет результат в swarm_memory и отправляет owner-у.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._path = state_path or _STATE_PATH
        self._jobs: dict[str, RecurringJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._sender: Callable[[str, str], Awaitable[None]] | None = None
        self._router_factory: Any = None
        self._owner_chat_id: str = ""
        self._started = False
        self._load()

    # -- lifecycle ------------------------------------------------------------

    def bind(
        self,
        *,
        sender: Callable[[str, str], Awaitable[None]],
        router_factory: Any,
        owner_chat_id: str,
    ) -> None:
        """Привязывает sender и router factory из userbot_bridge."""
        self._sender = sender
        self._router_factory = router_factory
        self._owner_chat_id = owner_chat_id

    def start(self) -> None:
        """Запускает все enabled jobs."""
        if self._started:
            return
        self._started = True
        if not config.SWARM_AUTONOMOUS_ENABLED:
            logger.info("swarm_scheduler_disabled", reason="SWARM_AUTONOMOUS_ENABLED=false")
            return
        for job_id, job in self._jobs.items():
            if job.enabled:
                self._schedule_job(job_id)
        logger.info("swarm_scheduler_started", jobs=len(self._jobs))

    def stop(self) -> None:
        """Останавливает все задачи."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._started = False
        self._save()
        logger.info("swarm_scheduler_stopped")

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            for item in data.get("jobs", []):
                job = RecurringJob.from_dict(item)
                if job.job_id:
                    self._jobs[job.job_id] = job
            logger.info("swarm_scheduler_loaded", jobs=len(self._jobs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_scheduler_load_failed", error=str(exc))

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "updated_at": _now_utc_iso(),
                "jobs": [asdict(j) for j in self._jobs.values()],
            }
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            logger.error("swarm_scheduler_save_failed", error=str(exc))

    # -- public API -----------------------------------------------------------

    def add_job(
        self,
        *,
        team: str,
        topic: str,
        interval_sec: int,
        workflow_type: str | WorkflowType = WorkflowType.STANDARD,
    ) -> RecurringJob:
        """Создаёт и запускает рекуррентный job.

        Args:
            team: команда свёрма (traders/coders/analysts/creative)
            topic: тема задачи
            interval_sec: интервал повторения в секундах
            workflow_type: тип workflow — standard/research/report
        """
        if not config.SWARM_AUTONOMOUS_ENABLED:
            raise RuntimeError(
                "Автономные задачи выключены. Включи: SWARM_AUTONOMOUS_ENABLED=1 в .env"
            )
        if len(self._jobs) >= _MAX_JOBS:
            raise RuntimeError(f"Максимум {_MAX_JOBS} рекуррентных задач")

        # Валидируем workflow_type
        try:
            wf = WorkflowType(workflow_type).value
        except ValueError:
            valid = ", ".join(w.value for w in WorkflowType)
            raise ValueError(f"Неизвестный workflow_type: {workflow_type!r}. Допустимые: {valid}")

        job = RecurringJob(
            job_id=uuid.uuid4().hex[:8],
            team=team.lower(),
            topic=topic.strip(),
            interval_sec=max(interval_sec, _MIN_INTERVAL_SEC),
            workflow_type=wf,
        )
        self._jobs[job.job_id] = job
        self._save()

        if self._started:
            self._schedule_job(job.job_id)

        logger.info(
            "swarm_scheduler_job_added", job_id=job.job_id, team=team, interval=interval_sec
        )
        return job

    def remove_job(self, job_id: str) -> bool:
        """Удаляет job."""
        job = self._jobs.pop(job_id, None)
        if not job:
            return False
        task = self._tasks.pop(job_id, None)
        if task and not task.done():
            task.cancel()
        self._save()
        logger.info("swarm_scheduler_job_removed", job_id=job_id)
        return True

    def list_jobs(self) -> list[RecurringJob]:
        """Возвращает все jobs."""
        return list(self._jobs.values())

    def format_jobs(self) -> str:
        """Форматирует список jobs для Telegram."""
        if not self._jobs:
            return "📅 Нет запланированных свёрм-задач."

        lines = ["📅 **Автономные свёрм-задачи:**\n"]
        for job in self._jobs.values():
            status = "✅" if job.enabled else "⏸️"
            interval_h = job.interval_sec / 3600
            interval_str = (
                f"{interval_h:.1f}ч" if interval_h >= 1 else f"{job.interval_sec // 60}мин"
            )
            wf_emoji = {"standard": "🔄", "research": "🔬", "report": "📊"}.get(
                job.workflow_type, "🔄"
            )
            lines.append(
                f"{status} `{job.job_id}` — **{job.team}** каждые {interval_str} {wf_emoji}`{job.workflow_type}`\n"
                f"  Тема: _{job.topic[:80]}_\n"
                f"  Прогонов: {job.total_runs} | "
                f"Последний: {job.last_run_at or '—'}"
            )
            if job.last_error:
                lines.append(f"  ⚠️ {job.last_error[:100]}")
            lines.append("")

        if not config.SWARM_AUTONOMOUS_ENABLED:
            lines.append("⚠️ **SWARM_AUTONOMOUS_ENABLED=false** — задачи не запускаются")

        lines.append("`!swarm unschedule <id>` — удалить задачу")
        return "\n".join(lines)

    def get_status(self) -> dict[str, Any]:
        """Статус для owner panel / diagnostics."""
        return {
            "enabled": config.SWARM_AUTONOMOUS_ENABLED,
            "total_jobs": len(self._jobs),
            "active_tasks": len(self._tasks),
            "jobs": [
                {
                    "job_id": j.job_id,
                    "team": j.team,
                    "topic": j.topic[:80],
                    "interval_sec": j.interval_sec,
                    "workflow_type": j.workflow_type,
                    "total_runs": j.total_runs,
                    "enabled": j.enabled,
                }
                for j in self._jobs.values()
            ],
        }

    # -- internal runner ------------------------------------------------------

    def _schedule_job(self, job_id: str) -> None:
        """Планирует следующий запуск job."""
        if not self._started:
            return
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return

        # Отменяем предыдущий task если есть
        old = self._tasks.pop(job_id, None)
        if old and not old.done():
            old.cancel()

        # Вычисляем delay до следующего запуска
        delay = float(job.interval_sec)
        if job.last_run_at:
            try:
                last = datetime.fromisoformat(job.last_run_at)
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                delay = max(10.0, job.interval_sec - elapsed)
            except (ValueError, TypeError):
                pass

        loop = asyncio.get_event_loop()
        self._tasks[job_id] = loop.create_task(self._run_job_loop(job_id, delay))

    async def _run_job_loop(self, job_id: str, initial_delay: float) -> None:
        """Бесконечный цикл: sleep → run → repeat."""
        try:
            await asyncio.sleep(initial_delay)
            while True:
                job = self._jobs.get(job_id)
                if not job or not job.enabled:
                    break

                await self._execute_job(job)

                await asyncio.sleep(float(job.interval_sec))
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.error("swarm_scheduler_job_loop_error", job_id=job_id, error=str(exc))
        finally:
            self._tasks.pop(job_id, None)

    async def _execute_job(self, job: RecurringJob) -> None:
        """Диспатчит job по workflow_type и обрабатывает ошибки."""
        logger.info(
            "swarm_scheduler_job_executing",
            job_id=job.job_id,
            team=job.team,
            workflow_type=job.workflow_type,
        )

        if not self._router_factory:
            job.last_error = "router_factory_not_bound"
            self._save()
            return

        try:
            if job.workflow_type == WorkflowType.RESEARCH:
                result = await self._execute_research_job(job)
            elif job.workflow_type == WorkflowType.REPORT:
                result = await self._execute_report_job(job)
            else:
                result = await self._execute_standard_job(job)

            job.total_runs += 1
            job.last_run_at = _now_utc_iso()
            job.last_error = ""
            self._save()

            # Отправляем результат owner-у
            if self._sender and self._owner_chat_id:
                wf_label = {"research": "🔬 Research", "report": "📊 Report"}.get(
                    job.workflow_type, "📅 Авто-прогон"
                )
                header = (
                    f"{wf_label} **#{job.total_runs}**\n"
                    f"Команда: {job.team} | Тема: {job.topic[:100]}\n\n"
                )
                msg = header + result
                # Обрезаем до лимита Telegram
                if len(msg) > 4000:
                    msg = msg[:3950] + "\n\n[...обрезано]"
                try:
                    await self._sender(self._owner_chat_id, msg)
                except Exception as send_exc:  # noqa: BLE001
                    logger.warning(
                        "swarm_scheduler_send_failed", job_id=job.job_id, error=str(send_exc)
                    )

            logger.info(
                "swarm_scheduler_job_completed", job_id=job.job_id, total_runs=job.total_runs
            )

        except Exception as exc:  # noqa: BLE001
            job.last_error = str(exc)[:200]
            job.last_run_at = _now_utc_iso()
            self._save()
            logger.error("swarm_scheduler_job_failed", job_id=job.job_id, error=str(exc))

            # Создаём inbox item при ошибке
            try:
                from .inbox_service import inbox_service

                inbox_service.upsert_item(
                    dedupe_key=f"swarm_job_failed:{job.job_id}",
                    kind="swarm_job_failure",
                    source="swarm_scheduler",
                    title=f"Swarm job {job.team} failed",
                    body=f"Job: {job.job_id}\nTeam: {job.team}\nTopic: {job.topic}\nError: {exc}",
                    severity="warning",
                    status="open",
                    identity=inbox_service.build_identity(
                        channel_id="swarm",
                        team_id=job.team,
                        trace_id=job.job_id,
                        approval_scope="owner",
                    ),
                    metadata={"job_id": job.job_id, "team": job.team},
                )
            except Exception:  # noqa: BLE001
                pass

    async def _execute_standard_job(self, job: RecurringJob) -> str:
        """Стандартный прогон свёрма — обычный AgentRoom.run_round()."""
        from .swarm import AgentRoom
        from .swarm_bus import TEAM_REGISTRY, swarm_bus

        roles = TEAM_REGISTRY.get(job.team)
        if not roles:
            raise RuntimeError(f"team_not_found:{job.team}")

        room = AgentRoom(roles=roles)
        router = self._router_factory(job.team)
        return await room.run_round(
            job.topic,
            router,
            _bus=swarm_bus,
            _router_factory=self._router_factory,
            _team_name=job.team,
        )

    async def _execute_research_job(self, job: RecurringJob) -> str:
        """Research pipeline: запуск analysts с принудительным web_search.

        Аналог команды !swarm research — промпт усиленный,
        результат сохраняется в artifact store.
        """
        from .swarm import AgentRoom
        from .swarm_bus import TEAM_REGISTRY, swarm_bus

        # Для research всегда используем analysts (они умеют web_search)
        team_key = "analysts"
        roles = TEAM_REGISTRY.get(team_key)
        if not roles:
            raise RuntimeError(f"team_not_found:{team_key}")

        # Усиленный промпт: обязателен web_search + структурированный результат
        research_topic = (
            f"Проведи исследование по теме: {job.topic}. "
            "Обязательно используй web_search для поиска актуальной информации. "
            "Структурируй результат: Summary, Key Findings, Sources."
        )

        room = AgentRoom(roles=roles)
        router = self._router_factory(team_key)
        result = await room.run_round(
            research_topic,
            router,
            _bus=swarm_bus,
            _router_factory=self._router_factory,
            _team_name=team_key,
        )

        # Сохраняем в artifact store
        try:
            from .swarm_artifact_store import swarm_artifact_store

            swarm_artifact_store.save_round_artifact(
                team=team_key,
                topic=f"[scheduled-research] {job.topic}",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_scheduler_research_artifact_save_failed", error=str(exc))

        return result

    async def _execute_report_job(self, job: RecurringJob) -> str:
        """Report pipeline: генерирует отчёт и сохраняет как артефакт.

        Промпт требует структурированного отчёта (Summary, Metrics, Recommendations).
        Результат всегда сохраняется в artifact store.
        """
        from .swarm import AgentRoom
        from .swarm_bus import TEAM_REGISTRY, swarm_bus

        roles = TEAM_REGISTRY.get(job.team)
        if not roles:
            raise RuntimeError(f"team_not_found:{job.team}")

        # Промпт для структурированного отчёта
        report_topic = (
            f"Подготовь структурированный отчёт по теме: {job.topic}. "
            "Включи разделы: Executive Summary, Ключевые метрики, Наблюдения, Рекомендации. "
            "Будь конкретен и лаконичен."
        )

        room = AgentRoom(roles=roles)
        router = self._router_factory(job.team)
        result = await room.run_round(
            report_topic,
            router,
            _bus=swarm_bus,
            _router_factory=self._router_factory,
            _team_name=job.team,
        )

        # Сохраняем отчёт в artifact store
        try:
            from .swarm_artifact_store import swarm_artifact_store

            swarm_artifact_store.save_round_artifact(
                team=job.team,
                topic=f"[scheduled-report] {job.topic}",
                result=result,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("swarm_scheduler_report_artifact_save_failed", error=str(exc))

        return result


# Singleton
swarm_scheduler = SwarmScheduler()
