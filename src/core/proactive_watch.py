# -*- coding: utf-8 -*-
"""
proactive_watch.py — фоновый owner-oriented watch и digest-слой Краба.

Что это:
- единый сервис, который собирает компактный runtime/macOS snapshot;
- умеет детектировать действительно важные изменения состояния;
- пишет digest в общую память OpenClaw и, при необходимости, уведомляет владельца.

Зачем нужно:
- reminders, health-check и memory уже существуют, но были разрозненными;
- владельцу нужен не "ещё один watchdog-скрипт", а связный снимок состояния;
- long-term память должна пополняться не только ручным `!remember`, но и
  короткими operational digest-записями, которые потом можно честно вспомнить.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

try:
    import sentry_sdk as _sentry_sdk
except ImportError:  # noqa: BLE001
    _sentry_sdk = None  # type: ignore[assignment]

from ..config import config
from ..integrations.macos_automation import macos_automation
from ..memory_engine import memory_manager
from ..openclaw_client import openclaw_client
from .anomaly_detector import Anomaly, anomaly_detector
from .auto_restart_policy import (
    RESTART_COMMANDS,
    auto_restart_manager,
    is_auto_restart_enabled,
)
from .inbox_service import inbox_service
from .logger import get_logger
from .observability import metrics as _observability_metrics
from .openclaw_runtime_models import get_runtime_primary_model
from .openclaw_workspace import append_workspace_memory_entry
from .scheduler import krab_scheduler
from .subprocess_env import clean_subprocess_env

# Prometheus metric для error digest — помогает увидеть fire-frequency
# (важно: audit выявил 0 fires за 49 рестартов, metric даст явную картину).
try:
    from prometheus_client import Counter as _Counter  # type: ignore

    _error_digest_fired_total = _Counter(
        "krab_error_digest_fired_total",
        "Количество успешных fires Error Digest (proactive_watch)",
        ["outcome"],  # outcome: ok|empty|failed
    )
except Exception:  # noqa: BLE001
    _error_digest_fired_total = None  # type: ignore[assignment]

# Порог «критических» ошибок для alert inbox_critical
_INBOX_CRITICAL_ERROR_THRESHOLD: int = 5

# Коэффициент «зависания» swarm job: если не запускалась дольше interval * N — алерт
_SWARM_STALL_FACTOR: float = 2.0

logger = get_logger(__name__)

# Wave 44-H: per-reason memory-dedup window и список reasons, исключённых
# из owner DM notify. `frontmost_app_changed` ранее давал 17 DM/день/30 memory
# writes — signal/noise 6%. Снижаем до ~4/день только в memory, без alert.
_FRONTMOST_MEMORY_DEDUP_SEC = 900  # 15 min
_NOTIFY_EXCLUDED_REASONS: set[str] = {"frontmost_app_changed"}

# Wave 44-N-watch: dreaming health monitor.
# Если diary не обновлялся > N секунд, но events.jsonl продолжает расти,
# считаем ingestion stuck (consolidation pipeline зависла).
_DREAMING_DIARY_STALE_SEC: int = 24 * 3600  # 24h
_DREAMING_EVENTS_PATH = (
    Path.home() / ".openclaw" / "workspace-main-messaging" / "memory" / ".dreams" / "events.jsonl"
)
_DREAMING_RECALL_PATH = (
    Path.home()
    / ".openclaw"
    / "workspace-main-messaging"
    / "memory"
    / ".dreams"
    / "short-term-recall.json"
)


def _now_utc_iso() -> str:
    """Возвращает timezone-aware UTC timestamp для snapshot/state."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_state_path() -> Path:
    """
    Возвращает per-account путь persisted state для proactive watch.

    Shared repo между несколькими macOS-учётками допустим, а mutable runtime-state нет:
    иначе фоновые loop'ы начинают падать на `Permission denied` и писать baseline
    в общий repo-level `data/`, где ownership зависит от предыдущей учётки.
    """
    return Path.home() / ".openclaw" / "krab_runtime_state" / "proactive_watch_state.json"


def _legacy_state_path() -> Path:
    """Старый repo-level путь сохраняем как read-only fallback на время миграции."""
    return config.BASE_DIR / "data" / "proactive_watch" / "state.json"


async def _fetch_openclaw_cron_jobs() -> list[dict[str, Any]]:
    """
    Получает список cron jobs из OpenClaw CLI (openclaw cron list --json --all).

    Возвращает пустой список при любой ошибке — не должна ронять proactive_watch.
    Вызов защищён семафором openclaw_cli_budget (budget=3).
    """
    from .openclaw_cli_budget import acquire as _cli_acquire
    from .openclaw_cli_budget import terminate_and_reap as _reap

    try:
        async with _cli_acquire():
            proc = await asyncio.create_subprocess_exec(
                "openclaw",
                "cron",
                "list",
                "--json",
                "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_subprocess_env(),
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            except asyncio.TimeoutError:
                await _reap(proc)
                return []
            raw = stdout.decode("utf-8", errors="replace").strip()
            payload = json.loads(raw)
            jobs = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
            return [j for j in jobs if isinstance(j, dict)]
    except Exception:  # noqa: BLE001
        return []


@dataclass
class ProactiveWatchSnapshot:
    """Компактный снимок живого состояния runtime для owner-digest."""

    ts_utc: str
    gateway_ok: bool
    primary_model: str
    route_channel: str
    route_provider: str
    route_model: str
    route_status: str
    route_reason: str
    scheduler_enabled: bool
    scheduler_started: bool
    scheduler_pending: int
    scheduler_next_due_at: str
    memory_count: int
    macos_available: bool
    macos_frontmost_app: str
    macos_frontmost_window: str
    reminder_lists_count: int
    note_folders_count: int
    calendars_count: int
    # Wave 44-N-watch: snapshot health Dreaming layer (опционально).
    dreaming_status: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProactiveWatchSnapshot":
        """Восстанавливает snapshot из persisted state."""
        ds = payload.get("dreaming_status")
        return cls(
            ts_utc=str(payload.get("ts_utc") or ""),
            gateway_ok=bool(payload.get("gateway_ok")),
            primary_model=str(payload.get("primary_model") or ""),
            route_channel=str(payload.get("route_channel") or ""),
            route_provider=str(payload.get("route_provider") or ""),
            route_model=str(payload.get("route_model") or ""),
            route_status=str(payload.get("route_status") or ""),
            route_reason=str(payload.get("route_reason") or ""),
            scheduler_enabled=bool(payload.get("scheduler_enabled")),
            scheduler_started=bool(payload.get("scheduler_started")),
            scheduler_pending=int(payload.get("scheduler_pending") or 0),
            scheduler_next_due_at=str(payload.get("scheduler_next_due_at") or ""),
            memory_count=int(payload.get("memory_count") or 0),
            macos_available=bool(payload.get("macos_available")),
            macos_frontmost_app=str(payload.get("macos_frontmost_app") or ""),
            macos_frontmost_window=str(payload.get("macos_frontmost_window") or ""),
            reminder_lists_count=int(payload.get("reminder_lists_count") or 0),
            note_folders_count=int(payload.get("note_folders_count") or 0),
            calendars_count=int(payload.get("calendars_count") or 0),
            dreaming_status=ds if isinstance(ds, dict) else None,
        )


class ProactiveWatchService:
    """
    Фоновый watch-сервис для owner runtime.

    Решение намеренно компактное:
    - не плодим отдельный daemon поверх уже существующего userbot runtime;
    - не спамим владельца на каждый poll;
    - считаем событием только реальные переходы состояния, а baseline просто
      сохраняем без уведомления.
    """

    def __init__(
        self,
        *,
        state_path: Path | None = None,
        alert_cooldown_sec: int | None = None,
    ) -> None:
        self.state_path = state_path or _default_state_path()
        # Legacy fallback нужен только для штатной миграции дефолтного пути.
        # Если вызывающий явно передал свой state_path (например, unit-тест),
        # не подмешиваем чужой repo-level state за его спиной.
        self.legacy_state_path = _legacy_state_path() if state_path is None else self.state_path
        self.alert_cooldown_sec = max(
            1800,
            int(
                alert_cooldown_sec
                or getattr(config, "PROACTIVE_WATCH_ALERT_COOLDOWN_SEC", 1800)
                or 1800
            ),
        )

    async def _read_dreaming_status(self) -> dict[str, Any] | None:
        """
        Возвращает текущий health-статус OpenClaw Dreaming layer.

        Стратегия:
        1) Пробуем gateway RPC `doctor.memory.status` через openclaw_client
           (если метод существует). Возвращает dict с полем `dreaming`.
        2) Fallback — читаем файлы напрямую:
           events.jsonl (line count + mtime) + short-term-recall.json
           (parse, count entries). Это не требует gateway.

        Возвращаемый формат:
            {
              "enabled": bool,
              "events_count": int,
              "recall_entries": int,
              "last_event_mtime": float | None,
              "error": str | None,
            }

        При полной неудаче чтения возвращает None.
        """
        # 1) Gateway RPC, если openclaw_client его поддерживает.
        rpc = getattr(openclaw_client, "doctor_memory_status", None)
        if callable(rpc):
            try:
                payload = await rpc()
                if isinstance(payload, dict):
                    dreaming = payload.get("dreaming")
                    if isinstance(dreaming, dict):
                        return {
                            "enabled": bool(dreaming.get("enabled", True)),
                            "events_count": int(dreaming.get("eventsCount") or 0),
                            "recall_entries": int(dreaming.get("recallEntries") or 0),
                            "last_event_mtime": dreaming.get("lastDiaryUpdate"),
                            "error": (
                                str(dreaming.get("error")) if dreaming.get("error") else None
                            ),
                        }
            except Exception as exc:  # noqa: BLE001
                logger.debug("dreaming_rpc_failed", error=str(exc))

        # 2) Fallback: filesystem inspection.
        events_path = _DREAMING_EVENTS_PATH
        recall_path = _DREAMING_RECALL_PATH
        if not events_path.exists() and not recall_path.exists():
            return None

        events_count = 0
        last_event_mtime: float | None = None
        recall_entries = 0
        error: str | None = None

        try:
            if events_path.exists():
                last_event_mtime = events_path.stat().st_mtime
                with events_path.open("r", encoding="utf-8", errors="replace") as fh:
                    events_count = sum(1 for _ in fh)
        except OSError as exc:
            error = f"events_read_failed:{exc}"

        try:
            if recall_path.exists():
                raw = recall_path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
                if isinstance(data, dict):
                    entries = data.get("entries") or data.get("items") or []
                    recall_entries = len(entries) if isinstance(entries, list) else 0
                elif isinstance(data, list):
                    recall_entries = len(data)
        except (OSError, ValueError) as exc:
            # Corrupt JSON — это сам по себе error-сигнал.
            error = (error + ";" if error else "") + f"recall_corrupt:{exc.__class__.__name__}"

        return {
            "enabled": True,
            "events_count": events_count,
            "recall_entries": recall_entries,
            "last_event_mtime": last_event_mtime,
            "error": error,
        }

    async def collect_snapshot(self) -> ProactiveWatchSnapshot:
        """Собирает живой snapshot из runtime, scheduler, памяти и macOS."""
        route = {}
        if hasattr(openclaw_client, "get_last_runtime_route"):
            try:
                route = openclaw_client.get_last_runtime_route() or {}
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_route_read_failed", error=str(exc))
                route = {}

        scheduler_status = krab_scheduler.get_status()
        macos_status: dict[str, Any] = {}
        if macos_automation.is_available():
            try:
                # Сигнатура status() больше не принимает apps_limit.
                # Держим вызов совместимым, чтобы proactive watch не спамил ложными warning.
                macos_status = await macos_automation.status()
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_macos_status_failed", error=str(exc))
                macos_status = {}

        gateway_ok = await openclaw_client.health_check()
        try:
            dreaming_status = await self._read_dreaming_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dreaming_status_read_failed", error=str(exc))
            dreaming_status = None
        return ProactiveWatchSnapshot(
            ts_utc=_now_utc_iso(),
            gateway_ok=bool(gateway_ok),
            primary_model=str(get_runtime_primary_model() or "").strip(),
            route_channel=str(route.get("channel") or "").strip(),
            route_provider=str(route.get("provider") or "").strip(),
            route_model=str(route.get("model") or "").strip(),
            route_status=str(route.get("status") or "").strip(),
            route_reason=str(route.get("route_reason") or "").strip(),
            scheduler_enabled=bool(getattr(config, "SCHEDULER_ENABLED", False)),
            scheduler_started=bool(getattr(krab_scheduler, "is_started", False)),
            scheduler_pending=int(scheduler_status.get("pending_count") or 0),
            scheduler_next_due_at=str(scheduler_status.get("next_due_at") or ""),
            memory_count=int(memory_manager.count()),
            macos_available=bool(macos_status.get("available")),
            macos_frontmost_app=str(macos_status.get("frontmost_app") or ""),
            macos_frontmost_window=str(macos_status.get("frontmost_window") or ""),
            reminder_lists_count=len(macos_status.get("reminder_lists") or []),
            note_folders_count=len(macos_status.get("note_folders") or []),
            calendars_count=len(macos_status.get("calendars") or []),
            dreaming_status=dreaming_status,
        )

    def _load_state(self) -> dict[str, Any]:
        """Читает persisted state безопасно и без падения всего runtime."""
        candidate_paths = [self.state_path]
        if self.legacy_state_path != self.state_path:
            candidate_paths.append(self.legacy_state_path)
        for candidate in candidate_paths:
            if not candidate.exists():
                continue
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning(
                    "proactive_watch_state_read_failed", path=str(candidate), error=str(exc)
                )
        return {}

    def _save_state(self, payload: dict[str, Any]) -> None:
        """Сохраняет latest state детерминированным JSON."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _detect_reason(
        previous: ProactiveWatchSnapshot | None,
        current: ProactiveWatchSnapshot,
    ) -> str:
        """Определяет, есть ли существенное изменение, достойное digest/alert."""
        if previous is None:
            return ""
        if previous.gateway_ok != current.gateway_ok:
            return "gateway_recovered" if current.gateway_ok else "gateway_down"
        if previous.route_model != current.route_model and current.route_model:
            return "route_model_changed"
        if previous.route_provider != current.route_provider and current.route_provider:
            return "route_provider_changed"
        if previous.scheduler_started != current.scheduler_started:
            return "scheduler_started" if current.scheduler_started else "scheduler_stopped"
        if previous.scheduler_pending == 0 and current.scheduler_pending > 0:
            return "scheduler_backlog_created"
        if previous.scheduler_pending > 0 and current.scheduler_pending == 0:
            return "scheduler_backlog_cleared"
        if (
            previous.macos_frontmost_app != current.macos_frontmost_app
            and current.macos_frontmost_app
        ):
            return "frontmost_app_changed"
        # Wave 44-N-watch: dreaming health detection.
        # Триггер 1: error в текущем статусе, которого не было в предыдущем
        # (или само поле появилось / изменилось).
        # Триггер 2: events.jsonl продолжает расти, но last_event_mtime
        # старше N секунд — ingestion stuck.
        cur = current.dreaming_status
        prev = previous.dreaming_status
        if isinstance(cur, dict):
            cur_error = cur.get("error")
            prev_error = (prev or {}).get("error") if isinstance(prev, dict) else None
            if cur_error and cur_error != prev_error:
                return "dreaming_error"
            # Stale diary: events grow, mtime > 24h.
            cur_events = int(cur.get("events_count") or 0)
            prev_events = (
                int((prev or {}).get("events_count") or 0) if isinstance(prev, dict) else 0
            )
            cur_mtime = cur.get("last_event_mtime")
            if cur_events > prev_events and isinstance(cur_mtime, (int, float)):
                age = time.time() - float(cur_mtime)
                if age > _DREAMING_DIARY_STALE_SEC:
                    return "dreaming_error"
        return ""

    @staticmethod
    def render_digest(snapshot: ProactiveWatchSnapshot, *, reason: str, manual: bool) -> str:
        """Формирует человекочитаемый owner-digest для Telegram/UI."""
        label = reason or ("manual_snapshot" if manual else "baseline")
        route_model = snapshot.route_model or snapshot.primary_model or "n/a"
        route_provider = snapshot.route_provider or "n/a"
        front_app = snapshot.macos_frontmost_app or "n/a"
        scheduler_line = f"{snapshot.scheduler_pending} pending" + (
            f", next `{snapshot.scheduler_next_due_at}`" if snapshot.scheduler_next_due_at else ""
        )
        dreaming_line = ""
        if reason == "dreaming_error" and isinstance(snapshot.dreaming_status, dict):
            err_desc = snapshot.dreaming_status.get("error") or "diary stale (>24h)"
            dreaming_line = f"\n🧠 OpenClaw Dreaming health alert: {err_desc}"
        return (
            "🦀 **Proactive Watch Digest**\n"
            f"- Причина: `{label}`\n"
            f"- Gateway: `{'ON' if snapshot.gateway_ok else 'OFF'}`\n"
            f"- Primary: `{snapshot.primary_model or 'n/a'}`\n"
            f"- Route: `{route_provider}` / `{route_model}` / `{snapshot.route_channel or 'n/a'}`\n"
            f"- Scheduler: `{scheduler_line}`\n"
            f"- Memory facts: `{snapshot.memory_count}`\n"
            f"- macOS: `{front_app}`"
            + (f" / `{snapshot.macos_frontmost_window}`" if snapshot.macos_frontmost_window else "")
            + "\n"
            f"- Sources: reminders `{snapshot.reminder_lists_count}`, notes `{snapshot.note_folders_count}`, calendars `{snapshot.calendars_count}`"
            + dreaming_line
        )

    @staticmethod
    def render_memory_entry(snapshot: ProactiveWatchSnapshot, *, reason: str, manual: bool) -> str:
        """Возвращает компактную one-line запись для общей workspace-memory."""
        label = reason or ("manual_snapshot" if manual else "baseline")
        route_model = snapshot.route_model or snapshot.primary_model or "n/a"
        return (
            f"watch={label}; gateway={'ON' if snapshot.gateway_ok else 'OFF'}; "
            f"primary={snapshot.primary_model or 'n/a'}; route={route_model}; "
            f"scheduler={snapshot.scheduler_pending}; memory={snapshot.memory_count}; "
            f"front={snapshot.macos_frontmost_app or 'n/a'}"
        )

    async def _check_and_trace_cron_executions(
        self, state: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """
        Детектирует новые выполнения OpenClaw cron jobs и создаёт inbox traces.

        Сравнивает last_run_at_ms каждой job с сохранённым значением в state.
        Возвращает обновлённый словарь last_cron_runs для записи в persisted state.
        Не бросает исключений — деградирует тихо при любых ошибках CLI/inbox.
        """
        jobs = await _fetch_openclaw_cron_jobs()
        if not jobs:
            return dict(state.get("last_cron_runs") or {})

        last_cron_runs: dict[str, dict[str, Any]] = dict(state.get("last_cron_runs") or {})
        updated: dict[str, dict[str, Any]] = {}

        for job in jobs:
            state_block = job.get("state") if isinstance(job.get("state"), dict) else {}
            job_id = str(job.get("id") or "").strip()
            if not job_id:
                continue
            job_name = str(job.get("name") or job_id).strip()
            last_run_at_ms = int(state_block.get("lastRunAtMs") or 0)
            last_status = str(
                state_block.get("lastStatus") or state_block.get("lastRunStatus") or "unknown"
            ).strip()
            last_error = str(
                state_block.get("lastError")
                or state_block.get("lastErrorMessage")
                or state_block.get("last_error")
                or ""
            ).strip()

            updated[job_id] = {"last_run_at_ms": last_run_at_ms, "last_status": last_status}

            prev_run_at = int((last_cron_runs.get(job_id) or {}).get("last_run_at_ms") or 0)
            if last_run_at_ms <= 0 or last_run_at_ms == prev_run_at:
                continue  # нет нового выполнения

            _ok_statuses = {"ok", "success", "succeeded", "completed"}
            is_ok = last_status.lower() in _ok_statuses
            # Wave 44-H: benign env-level conflict — gateway respawn naturally
            # kills long-running codex-cli jobs (Nightly Self-Diagnostics fails
            # 8/15 runs at 03:00). Не actionable, downgrade severity до info.
            _benign_error_markers = ("cron: job interrupted by gateway restart",)
            is_benign_interrupt = any(marker in last_error for marker in _benign_error_markers)
            if last_status.lower() in ("error", "failed", "failure"):
                severity = "info" if is_benign_interrupt else "warning"
            else:
                severity = "info"
            # Статус inbox item: успешный запуск → сразу закрываем (done),
            # ошибка → оставляем open для owner review.
            item_status = "done" if is_ok else "open"
            run_ts = datetime.fromtimestamp(last_run_at_ms / 1000, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
            # dedupe_key без timestamp — один item на job, upsert обновляет его,
            # а не создаёт дубль при каждом запуске cron.
            dedupe_key = f"proactive:cron_run:{job_id}"
            try:
                inbox_service.upsert_item(
                    dedupe_key=dedupe_key,
                    kind="proactive_action",
                    source="krab-internal",
                    title=f"Cron job выполнен: {job_name}",
                    body=(
                        f"Job `{job_name}` (id={job_id}) выполнен в {run_ts}. "
                        f"Статус: `{last_status}`."
                    ),
                    severity=severity,
                    status=item_status,
                    identity=inbox_service.build_identity(
                        channel_id="system",
                        team_id="owner",
                        trace_id=f"cron:{job_id}",
                        approval_scope="owner",
                    ),
                    metadata={
                        "action_type": "cron_execution",
                        "job_id": job_id,
                        "job_name": job_name,
                        "last_run_at_ms": last_run_at_ms,
                        "last_status": last_status,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("cron_trace_upsert_failed", job_id=job_id, error=str(exc))

        return updated

    async def capture(
        self,
        *,
        manual: bool = False,
        persist_memory: bool = True,
        notify: bool = False,
        notifier: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """
        Снимает snapshot, сравнивает его с baseline и при необходимости шлёт alert.

        manual=True:
        - всегда возвращает digest;
        - пишет запись в workspace-memory даже без смены состояния;
        - не считает baseline тишиной.
        """
        state = self._load_state()
        previous_payload = state.get("last_snapshot")
        previous = (
            ProactiveWatchSnapshot.from_dict(previous_payload)
            if isinstance(previous_payload, dict)
            else None
        )
        snapshot = await self.collect_snapshot()
        reason = self._detect_reason(previous, snapshot)
        digest = self.render_digest(snapshot, reason=reason, manual=manual)
        wrote_memory = False
        alerted = False

        # Трейс выполнений OpenClaw cron jobs (Phase 2.3)
        try:
            updated_cron_runs = await self._check_and_trace_cron_executions(state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cron_trace_check_failed", error=str(exc))
            updated_cron_runs = dict(state.get("last_cron_runs") or {})

        # Janitor: sweep stale `acked` items (allowlist: proactive_action + owner_request) → `done`
        # (Wave 8-A + Wave 9-C safety net: cron handler / background processing
        # / LLM workflows create acked traces but never transition them; without
        # this items pile up as stale_processing forever — см. Wave 7-C / 8-B / Session 33).
        try:
            sweep_result = inbox_service.sweep_acked_proactive_actions(
                age_threshold_minutes=60,
                actor="proactive-watch-janitor",
            )
            if sweep_result.get("swept"):
                logger.info(
                    "inbox_janitor_swept",
                    swept=sweep_result["swept"],
                    matched=sweep_result.get("matched", 0),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox_janitor_failed", error=str(exc))

        # Wave 44-H: per-reason memory dedup window. Для шумных reasons
        # (frontmost_app_changed) пропускаем write если последний был < N сек.
        memory_writes_per_reason: dict[str, str] = dict(
            state.get("last_memory_write_per_reason") or {}
        )
        skip_memory_write = False
        if persist_memory and not manual and reason == "frontmost_app_changed":
            prev_ts = memory_writes_per_reason.get(reason) or ""
            if prev_ts:
                try:
                    elapsed = (
                        datetime.now(timezone.utc) - datetime.fromisoformat(prev_ts)
                    ).total_seconds()
                    if elapsed < _FRONTMOST_MEMORY_DEDUP_SEC:
                        skip_memory_write = True
                except ValueError:
                    pass

        if persist_memory and (manual or bool(reason)) and not skip_memory_write:
            wrote_memory = append_workspace_memory_entry(
                self.render_memory_entry(snapshot, reason=reason, manual=manual),
                source="proactive-watch",
            )
            if wrote_memory and reason:
                memory_writes_per_reason[reason] = snapshot.ts_utc

        last_alert_ts = str(state.get("last_alert_ts") or "")
        last_alerted_reason = str(state.get("last_alerted_reason") or "")
        # last_gateway_alert_ts — отдельный cooldown для gateway событий (down+recovered).
        # Без него down→recovered→down чередование обходит per-reason cooldown и
        # вызывает 19× DM spam при sleep/wake или нестабильной сети.
        last_gateway_alert_ts = str(state.get("last_gateway_alert_ts") or "")
        cooldown_ok = True
        _gateway_reasons = {"gateway_down", "gateway_recovered"}
        if last_alert_ts and last_alerted_reason == reason:
            try:
                elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_alert_ts)
                cooldown_ok = elapsed.total_seconds() >= self.alert_cooldown_sec
            except ValueError:
                cooldown_ok = True
        # Дополнительный глобальный gateway cooldown — независимо от смены reason.
        if cooldown_ok and reason in _gateway_reasons and last_gateway_alert_ts:
            try:
                gw_elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(
                    last_gateway_alert_ts
                )
                if gw_elapsed.total_seconds() < self.alert_cooldown_sec:
                    cooldown_ok = False
                    logger.debug(
                        "proactive_watch_gateway_cooldown_active",
                        reason=reason,
                        elapsed_sec=round(gw_elapsed.total_seconds()),
                        cooldown_sec=self.alert_cooldown_sec,
                    )
            except ValueError:
                pass

        # Wave 44-H: исключаем low-signal reasons из owner DM (frontmost_app_changed).
        # Memory write остаётся (с dedup), trace/inbox остаются — только DM не идёт.
        notify_allowed_for_reason = reason not in _NOTIFY_EXCLUDED_REASONS
        if notify and reason and notifier is not None and cooldown_ok and notify_allowed_for_reason:
            try:
                await notifier(digest)
                alerted = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_notify_failed", reason=reason, error=str(exc))

        payload = {
            "last_snapshot": asdict(snapshot),
            "last_reason": reason,
            "last_digest_ts": snapshot.ts_utc
            if (manual or reason)
            else str(state.get("last_digest_ts") or ""),
            "last_alert_ts": snapshot.ts_utc if alerted else last_alert_ts,
            "last_alerted_reason": reason
            if alerted
            else str(state.get("last_alerted_reason") or ""),
            "last_cron_runs": updated_cron_runs,
            # Обновляем gateway cooldown timestamp при любом gateway alert.
            "last_gateway_alert_ts": snapshot.ts_utc
            if (alerted and reason in _gateway_reasons)
            else last_gateway_alert_ts,
            "last_memory_write_per_reason": memory_writes_per_reason,
        }
        self._save_state(payload)
        if reason:
            try:
                inbox_service.report_watch_transition(
                    reason=reason,
                    digest=digest,
                    snapshot=asdict(snapshot),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_inbox_sync_failed", reason=reason, error=str(exc))

            # `proactive_action` нужен как owner-visible trace именно для активной проблемы.
            # Recovery-событие должно закрывать открытый trace проблемы, а не оставлять ещё
            # один `open` item с пометкой "всё восстановилось".
            open_trace_reasons = {
                "gateway_down",
                "scheduler_backlog_created",
            }
            close_trace_reasons = {
                "gateway_recovered": "gateway_down",
                "scheduler_backlog_cleared": "scheduler_backlog_created",
            }
            if reason in open_trace_reasons:
                try:
                    inbox_service.upsert_item(
                        dedupe_key=f"proactive:watch_trigger:{reason}",
                        kind="proactive_action",
                        source="krab-internal",
                        title=f"Proactive watch: {reason}",
                        body=digest,
                        severity="info",
                        status="open",
                        identity=inbox_service.build_identity(
                            channel_id="system",
                            team_id="owner",
                            trace_id=f"watch:{reason}",
                            approval_scope="owner",
                        ),
                        metadata={
                            "action_type": "watch_trigger",
                            "reason": reason,
                            "alerted": alerted,
                            "latest_snapshot_ts": snapshot.ts_utc,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "proactive_watch_action_trace_failed", reason=reason, error=str(exc)
                    )
            elif reason in close_trace_reasons:
                try:
                    inbox_service.set_status_by_dedupe(
                        f"proactive:watch_trigger:{close_trace_reasons[reason]}",
                        status="done",
                        actor="krab-internal",
                        note=f"watch_recovered:{reason}",
                        event_action="resolved",
                        metadata_updates={
                            "recovered_reason": reason,
                            "latest_snapshot_ts": snapshot.ts_utc,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "proactive_watch_action_trace_close_failed", reason=reason, error=str(exc)
                    )
                # Gateway вернулся → сбрасываем stale consecutive_failures в failover_policy
                # чтобы следующий запрос не упирался в порог и шёл напрямую без блокировки.
                if reason == "gateway_recovered":
                    try:
                        from .provider_failover import failover_policy  # noqa: PLC0415

                        failover_policy.reset()
                        logger.info("failover_policy_reset_on_gateway_recovery")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("failover_policy_reset_failed", error=str(exc))
        return {
            "snapshot": asdict(snapshot),
            "reason": reason,
            "digest": digest,
            "alerted": alerted,
            "wrote_memory": wrote_memory,
            "manual": manual,
            "baseline_created": previous is None,
        }

    # -----------------------------------------------------------------
    # Auto-restart of external services (session 10)
    # -----------------------------------------------------------------
    # Карта: имя сервиса → (URL health check). Krab core / LM Studio
    # намеренно не включены — self-restart и пользовательский UI.
    AUTO_RESTART_HEALTH_URLS: dict[str, str] = {
        "openclaw_gateway": "http://127.0.0.1:18789/health",
        "mcp_yung_nagato": "http://127.0.0.1:8011/",
        "mcp_p0lrd": "http://127.0.0.1:8012/",
        "mcp_hammerspoon": "http://127.0.0.1:8013/",
    }
    AUTO_RESTART_HEALTH_TIMEOUT_SEC: float = 3.0
    AUTO_RESTART_CHECKS_INTERVAL_SEC: int = 300  # 5 минут

    async def _probe_service_health(self, url: str) -> bool:
        """
        Быстрый HTTP-probe. Возвращает True если GET вернул 2xx в срок.
        Любая ошибка считается fail (сервис мёртв).
        """
        try:
            import httpx  # локальный импорт: в тестах можно замокать
        except ImportError:
            logger.warning("auto_restart_httpx_missing")
            return True  # не мы решаем: пусть остальные слои детектят

        try:
            async with httpx.AsyncClient(timeout=self.AUTO_RESTART_HEALTH_TIMEOUT_SEC) as c:
                resp = await c.get(url)
                return 200 <= resp.status_code < 300
        except Exception as exc:  # noqa: BLE001
            logger.info("auto_restart_probe_failed", url=url, error=str(exc))
            return False

    async def run_auto_restart_checks(self) -> dict[str, Any]:
        """
        Прогоняет health-probe по всем auto-restart-eligible сервисам и
        пытается поднять упавшие через auto_restart_manager.

        Безопасно по умолчанию: если AUTO_RESTART_ENABLED=false (дефолт),
        всё равно возвращаем health-report, но без вызова restart.
        """
        results: dict[str, Any] = {}
        for service, url in self.AUTO_RESTART_HEALTH_URLS.items():
            ok = await self._probe_service_health(url)
            entry: dict[str, Any] = {"healthy": ok, "url": url}
            if not ok:
                cmd = RESTART_COMMANDS.get(service)
                if cmd is None:
                    entry["restart"] = {"attempted": False, "reason": "no_cmd"}
                else:
                    success, reason = await auto_restart_manager.attempt_restart(service, cmd)
                    entry["restart"] = {
                        "attempted": True,
                        "success": success,
                        "reason": reason,
                    }
            results[service] = entry

        logger.info(
            "auto_restart_checks_done",
            enabled=is_auto_restart_enabled(),
            results=results,
        )
        return results

    async def _auto_restart_checks_loop(self) -> None:
        """Бесконечный цикл auto-restart-health: каждые N секунд."""
        while True:
            await asyncio.sleep(self.AUTO_RESTART_CHECKS_INTERVAL_SEC)
            try:
                await self.run_auto_restart_checks()
            except Exception as exc:  # noqa: BLE001
                logger.warning("auto_restart_loop_error", error=str(exc))
                if _sentry_sdk is not None:
                    _sentry_sdk.capture_exception(exc)

    def start_auto_restart_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу auto-restart и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._auto_restart_checks_loop(), name="krab_auto_restart_checks")
        logger.info(
            "auto_restart_loop_started",
            interval_sec=self.AUTO_RESTART_CHECKS_INTERVAL_SEC,
            enabled=is_auto_restart_enabled(),
        )
        return task

    def get_status(self) -> dict[str, Any]:
        """Возвращает persisted статус watch-контура для команд/UI."""
        state = self._load_state()
        snapshot = (
            state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
        )
        return {
            "enabled": config.PROACTIVE_WATCH_ENABLED,
            "interval_sec": config.PROACTIVE_WATCH_INTERVAL_SEC,
            "alert_cooldown_sec": self.alert_cooldown_sec,
            "last_reason": str(state.get("last_reason") or ""),
            "last_digest_ts": str(state.get("last_digest_ts") or ""),
            "last_alert_ts": str(state.get("last_alert_ts") or ""),
            "last_alerted_reason": str(state.get("last_alerted_reason") or ""),
            "last_snapshot": snapshot,
        }

    # Интервал Error Digest в секундах (24 часа — downgrade с 6h после аудита:
    # 0 fires за 49 рестартов сказали что 6h избыточно; 24h совпадает со scope
    # weekly_digest и покрывает типичный рабочий цикл).
    ERROR_DIGEST_INTERVAL_SEC: int = 86400
    # Первый fire — через короткую задержку после старта (а не через 24h),
    # чтобы при частых рестартах loop всё равно успевал стрельнуть хотя бы раз.
    # Аналог FIRST_RUN_DELAY_SEC в weekly_digest. Идемпотентность обеспечивается
    # dedupe_key `proactive:error_digest:YYYY-MM-DDTHH` в inbox_service.
    ERROR_DIGEST_FIRST_RUN_DELAY_SEC: int = 300
    # Максимум ошибок в сводке
    ERROR_DIGEST_MAX_ITEMS: int = 10

    async def run_error_digest(self) -> dict[str, Any]:
        """
        Собирает сводку открытых ошибок/предупреждений из inbox и записывает
        digest-item. Вызывается периодически (каждые 6 часов).

        Не бросает исключений — деградирует тихо при любых сбоях inbox.
        """
        try:
            # Собираем открытые warning/error items
            open_items = inbox_service.list_items(status="open", limit=200)
        except Exception as exc:  # noqa: BLE001
            logger.warning("error_digest_list_failed", error=str(exc))
            return {"ok": False, "error": str(exc)}

        # Фильтруем по severity
        error_items = [it for it in open_items if it.get("severity") in ("error", "warning")]

        total = len(error_items)
        # Подсчёт по severity
        counts: dict[str, int] = {}
        for it in error_items:
            sev = str(it.get("severity") or "unknown")
            counts[sev] = counts.get(sev, 0) + 1

        # Берём последние N (список отсортирован старые→новые, берём хвост)
        recent = error_items[-self.ERROR_DIGEST_MAX_ITEMS :]

        # Формируем тело сводки
        ts_now = _now_utc_iso()
        counts_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        lines = [f"**Error Digest (6h)** — {ts_now}"]
        lines.append(f"Открытых issues: {total} ({counts_str or 'нет'})")
        if recent:
            lines.append("\nПоследние:")
            for it in recent:
                sev = it.get("severity", "?")
                title = str(it.get("title") or "—")[:80]
                created = str(it.get("created_at_utc") or "")[:19]
                lines.append(f"- [{sev}] {title} ({created})")
        else:
            lines.append("Нет открытых ошибок/предупреждений.")

        body = "\n".join(lines)

        try:
            inbox_service.upsert_item(
                dedupe_key=f"proactive:error_digest:{ts_now[:13]}",  # уникально по часу
                kind="proactive_action",
                source="krab-internal",
                title=f"Error Digest (6h): {total} open issues",
                body=body,
                severity="info",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id="system",
                    team_id="owner",
                    trace_id="error_digest",
                    approval_scope="owner",
                ),
                metadata={
                    "action_type": "error_digest",
                    "total_issues": total,
                    "counts_by_severity": counts,
                    "digest_ts": ts_now,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("error_digest_upsert_failed", error=str(exc))
            if _error_digest_fired_total is not None:
                try:
                    _error_digest_fired_total.labels(outcome="failed").inc()
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": str(exc)}

        logger.info("error_digest_written", total=total, counts=counts)
        # Prometheus counter — различаем ok/empty/failed outcome для чтения
        # /metrics и понимания реальной активности.
        if _error_digest_fired_total is not None:
            try:
                outcome = "empty" if total == 0 else "ok"
                _error_digest_fired_total.labels(outcome=outcome).inc()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "total": total, "counts": counts, "digest_ts": ts_now}

    async def _error_digest_loop(self) -> None:
        """Бесконечный цикл: 1 раз в 24 часа (downgrade с 6h после аудита).

        ВАЖНО: первый fire — через ERROR_DIGEST_FIRST_RUN_DELAY_SEC (5 мин) после
        старта, не через 24h. Иначе при частых рестартах loop никогда не доходит
        до первого fire. Идемпотентность — через dedupe_key в inbox_service.
        """
        # Короткая задержка перед первым fire — даём userbot полностью подняться
        await asyncio.sleep(self.ERROR_DIGEST_FIRST_RUN_DELAY_SEC)
        while True:
            try:
                await self.run_error_digest()
            except Exception as exc:  # noqa: BLE001
                logger.warning("error_digest_loop_error", error=str(exc))
            await asyncio.sleep(self.ERROR_DIGEST_INTERVAL_SEC)

    def start_error_digest_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу Error Digest и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._error_digest_loop(), name="krab_error_digest")
        logger.info("error_digest_loop_started", interval_sec=self.ERROR_DIGEST_INTERVAL_SEC)
        return task

    # Интервал проверки alert-условий (30 минут)
    ALERT_CHECKS_INTERVAL_SEC: int = 1800

    async def run_alert_checks(self) -> dict[str, Any]:
        """
        Проверяет alert-условия и создаёт inbox items при срабатывании.

        Алерты:
        - inbox_critical: >5 открытых items с severity=error → уведомление владельца.
        - swarm_job_stalled: swarm job не запускалась дольше interval*2 → уведомление.

        Не бросает исключений — деградирует тихо.
        Возвращает словарь {alert_name: triggered (bool)} для каждого алерта.
        """
        results: dict[str, Any] = {}

        # -- inbox_critical -------------------------------------------------------
        try:
            results["inbox_critical"] = await self._check_inbox_critical()
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert_check_inbox_critical_failed", error=str(exc))
            results["inbox_critical"] = False

        # -- swarm_job_stalled ----------------------------------------------------
        try:
            results["swarm_job_stalled"] = await self._check_swarm_job_stalled()
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert_check_swarm_job_stalled_failed", error=str(exc))
            results["swarm_job_stalled"] = False

        # -- cost_budget ----------------------------------------------------------
        try:
            results["cost_budget"] = self._check_cost_budget()
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert_check_cost_budget_failed", error=str(exc))
            results["cost_budget"] = False

        # -- archive_db_size ------------------------------------------------------
        try:
            results["archive_db_size"] = self._check_archive_db_size()
        except Exception as exc:  # noqa: BLE001
            logger.warning("alert_check_archive_db_size_failed", error=str(exc))
            results["archive_db_size"] = False

        logger.info("alert_checks_done", results=results)
        return results

    async def _check_inbox_critical(self) -> bool:
        """
        Если открытых items с severity=error больше порога — пишет алерт в inbox.
        Возвращает True если алерт сработал.
        """
        open_items = inbox_service.list_items(status="open", limit=500)
        error_items = [it for it in open_items if it.get("severity") == "error"]
        count = len(error_items)

        if count <= _INBOX_CRITICAL_ERROR_THRESHOLD:
            return False

        ts_now = _now_utc_iso()
        titles = [str(it.get("title") or "—")[:60] for it in error_items[:5]]
        body_lines = [f"**Inbox Critical Alert** — {ts_now}"]
        body_lines.append(
            f"Открытых error-items: {count} (порог: {_INBOX_CRITICAL_ERROR_THRESHOLD})"
        )
        body_lines.append("\nПримеры:")
        for t in titles:
            body_lines.append(f"- {t}")

        inbox_service.upsert_item(
            dedupe_key="proactive:alert:inbox_critical",
            kind="proactive_action",
            source="krab-internal",
            title=f"Inbox Critical: {count} открытых ошибок",
            body="\n".join(body_lines),
            severity="error",
            status="open",
            identity=inbox_service.build_identity(
                channel_id="system",
                team_id="owner",
                trace_id="alert:inbox_critical",
                approval_scope="owner",
            ),
            metadata={
                "action_type": "inbox_critical_alert",
                "error_count": count,
                "threshold": _INBOX_CRITICAL_ERROR_THRESHOLD,
                "alert_ts": ts_now,
            },
        )
        logger.warning("alert_inbox_critical_triggered", error_count=count)
        return True

    async def _check_swarm_job_stalled(self) -> bool:
        """
        Проверяет все рекуррентные swarm jobs.

        Job считается зависшей, если:
        - enabled=True;
        - last_run_at задан (уже запускалась хотя бы раз);
        - прошло больше interval_sec * _SWARM_STALL_FACTOR секунд без нового запуска.

        Для каждой зависшей job создаёт отдельный inbox item (dedupe по job_id).
        Возвращает True если хотя бы один алерт сработал.
        """
        from .swarm_scheduler import swarm_scheduler  # ленивый импорт (избегаем циклов)

        jobs = swarm_scheduler.list_jobs()
        now_ts = datetime.now(timezone.utc)
        triggered_any = False

        for job in jobs:
            if not job.enabled:
                continue
            last_run_at_str = str(job.last_run_at or "").strip()
            if not last_run_at_str:
                # Job ещё ни разу не запускалась — не считаем зависшей
                continue
            interval_sec = int(job.interval_sec or 0)
            if interval_sec <= 0:
                continue

            try:
                last_run_dt = datetime.fromisoformat(last_run_at_str)
                # Убеждаемся что datetime timezone-aware
                if last_run_dt.tzinfo is None:
                    last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            elapsed = (now_ts - last_run_dt).total_seconds()
            stall_threshold = interval_sec * _SWARM_STALL_FACTOR

            if elapsed < stall_threshold:
                continue

            # Job зависла
            job_id = str(job.job_id or "unknown")
            team = str(job.team or "?")
            topic = str(job.topic or "?")
            elapsed_min = int(elapsed // 60)
            expected_min = int(stall_threshold // 60)

            inbox_service.upsert_item(
                dedupe_key=f"proactive:alert:swarm_stalled:{job_id}",
                kind="proactive_action",
                source="krab-internal",
                title=f"Swarm Job Stalled: {team} / {topic[:40]}",
                body=(
                    f"**Swarm Job Stalled** — job_id={job_id}\n"
                    f"Команда: `{team}`, тема: `{topic}`\n"
                    f"Не запускался {elapsed_min} мин (ожидаемо каждые {expected_min} мин).\n"
                    f"Последний запуск: {last_run_at_str}"
                ),
                severity="warning",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id="system",
                    team_id="owner",
                    trace_id=f"alert:swarm_stalled:{job_id}",
                    approval_scope="owner",
                ),
                metadata={
                    "action_type": "swarm_job_stalled_alert",
                    "job_id": job_id,
                    "team": team,
                    "topic": topic,
                    "elapsed_sec": int(elapsed),
                    "interval_sec": interval_sec,
                    "stall_threshold_sec": int(stall_threshold),
                    "last_run_at": last_run_at_str,
                },
            )
            logger.warning(
                "alert_swarm_job_stalled",
                job_id=job_id,
                elapsed_min=elapsed_min,
            )
            triggered_any = True

        return triggered_any

    def _check_cost_budget(self) -> bool:
        """
        Проверяет расходы относительно месячного бюджета.

        >80% → warning, >100% → error. Dedupe по месяцу.
        Возвращает True если алерт сработал.
        """
        from .cost_analytics import cost_analytics as _ca  # noqa: PLC0415

        budget = _ca.get_monthly_budget_usd()
        if budget <= 0:
            return False  # бюджет не установлен
        spent = _ca.get_monthly_cost_usd()
        pct = spent / budget * 100

        if pct < 80:
            return False

        import datetime as _dt  # noqa: PLC0415

        month_label = _dt.date.today().strftime("%Y-%m")
        severity = "error" if pct >= 100 else "warning"
        ts_now = _now_utc_iso()

        inbox_service.upsert_item(
            dedupe_key=f"proactive:alert:cost_budget:{month_label}",
            kind="proactive_action",
            source="krab-internal",
            title=f"Cost Budget {'Exceeded' if pct >= 100 else 'Warning'}: {pct:.0f}%",
            body=(
                f"**Cost Budget Alert** — {ts_now}\n"
                f"Потрачено: ${spent:.4f} из ${budget:.2f} ({pct:.1f}%)\n"
                f"Осталось: ${max(0, budget - spent):.4f}"
            ),
            severity=severity,
            status="open",
            identity=inbox_service.build_identity(
                channel_id="system",
                team_id="owner",
                trace_id=f"alert:cost_budget:{month_label}",
                approval_scope="owner",
            ),
            metadata={
                "action_type": "cost_budget_alert",
                "spent_usd": round(spent, 6),
                "budget_usd": budget,
                "used_pct": round(pct, 2),
                "alert_ts": ts_now,
            },
        )
        logger.warning(
            "alert_cost_budget_triggered",
            spent=round(spent, 4),
            budget=budget,
            pct=round(pct, 1),
        )
        return True

    # -----------------------------------------------------------------
    # OpenClaw gateway health alert (consecutive failures → owner alert)
    # -----------------------------------------------------------------
    # Интервал между пробами (секунды)
    OPENCLAW_HEALTH_CHECK_INTERVAL_SEC: int = 10
    # Количество последовательных сбоев до отправки алерта
    OPENCLAW_HEALTH_FAIL_THRESHOLD: int = 3
    # Debounce: минимальный интервал между алертами (30 минут)
    OPENCLAW_HEALTH_ALERT_DEBOUNCE_SEC: int = 1800

    @staticmethod
    def _is_openclaw_health_alert_enabled() -> bool:
        """Gate: KRAB_OPENCLAW_HEALTH_ALERT_ENABLED, default on."""
        raw = os.environ.get("KRAB_OPENCLAW_HEALTH_ALERT_ENABLED", "1").strip().lower()
        return raw in ("1", "true", "yes", "on")

    async def _check_openclaw_gateway_unreachable(
        self,
        notifier: Callable[[str], Awaitable[None]] | None = None,
        *,
        _consecutive_failures: list[int],
    ) -> dict[str, Any]:
        """
        Проверяет доступность OpenClaw gateway.

        Параметры:
          notifier — async callback для отправки Telegram alert владельцу.
          _consecutive_failures — mutable list[int] с единственным элементом —
            счётчиком последовательных сбоев (in-out аргумент через список).

        Логика:
          - Каждый вызов делает один health probe.
          - При сбое инкрементирует счётчик.
          - При OPENCLAW_HEALTH_FAIL_THRESHOLD последовательных сбоях:
            - создаёт inbox item;
            - если notifier доступен и прошёл debounce — отправляет Telegram alert.
          - При успехе сбрасывает счётчик и при восстановлении после алерта — recovery alert.
          - Возвращает dict {ok, consecutive_failures, alerted}.
        """
        if not self._is_openclaw_health_alert_enabled():
            return {"ok": True, "consecutive_failures": 0, "alerted": False, "disabled": True}

        ok = await self._probe_service_health("http://127.0.0.1:18789/health")
        state = self._load_state()
        prev_failures = _consecutive_failures[0]
        prev_alerted = bool(state.get("openclaw_health_alert_active", False))
        last_alert_ts = float(state.get("openclaw_health_last_alert_ts", 0))
        ts_now = _now_utc_iso()
        alerted = False

        if ok:
            # Gateway доступен
            if prev_failures > 0:
                logger.info(
                    "openclaw_gateway_health_recovered",
                    after_failures=prev_failures,
                )
            _consecutive_failures[0] = 0
            # Если был активный алерт — отправим recovery уведомление
            if prev_alerted:
                state["openclaw_health_alert_active"] = False
                self._save_state(state)
                # Закрываем inbox item
                try:
                    inbox_service.set_status_by_dedupe(
                        "proactive:alert:openclaw_gateway_unreachable",
                        status="done",
                        actor="krab-internal",
                        note="gateway_health_recovered",
                        event_action="resolved",
                        metadata_updates={"recovered_at": ts_now},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("openclaw_health_recovery_inbox_close_failed", error=str(exc))
                if notifier is not None:
                    try:
                        await notifier(
                            "✅ **OpenClaw Gateway: восстановлен**\n"
                            f"Gateway `http://127.0.0.1:18789` снова отвечает (был недоступен {prev_failures} проб)."
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("openclaw_health_recovery_alert_failed", error=str(exc))
            return {"ok": True, "consecutive_failures": 0, "alerted": False}

        # Gateway недоступен
        _consecutive_failures[0] = prev_failures + 1
        new_failures = _consecutive_failures[0]
        logger.warning(
            "openclaw_gateway_health_probe_failed",
            consecutive_failures=new_failures,
            threshold=self.OPENCLAW_HEALTH_FAIL_THRESHOLD,
        )

        if new_failures < self.OPENCLAW_HEALTH_FAIL_THRESHOLD:
            # Ещё не достигли порога — молчим
            return {"ok": False, "consecutive_failures": new_failures, "alerted": False}

        # Порог достигнут — проверяем debounce
        now_ts = time.time()
        debounce_elapsed = now_ts - last_alert_ts
        debounce_ok = debounce_elapsed >= self.OPENCLAW_HEALTH_ALERT_DEBOUNCE_SEC

        # Создаём/обновляем inbox item при каждом срабатывании порога
        alert_body = (
            f"**OpenClaw Gateway Unreachable** — {ts_now}\n"
            f"Gateway `http://127.0.0.1:18789` не отвечает {new_failures} проб подряд "
            f"(порог: {self.OPENCLAW_HEALTH_FAIL_THRESHOLD}).\n"
            "Возможные причины: crash gateway-процесса, занятый порт, OOM.\n"
            "Действие: `openclaw gateway` для рестарта (НЕ SIGHUP)."
        )
        try:
            inbox_service.upsert_item(
                dedupe_key="proactive:alert:openclaw_gateway_unreachable",
                kind="proactive_action",
                source="krab-internal",
                title=f"OpenClaw Gateway недоступен ({new_failures} сбоев подряд)",
                body=alert_body,
                severity="error",
                status="open",
                identity=inbox_service.build_identity(
                    channel_id="system",
                    team_id="owner",
                    trace_id="alert:openclaw_gateway_unreachable",
                    approval_scope="owner",
                ),
                metadata={
                    "action_type": "openclaw_gateway_unreachable_alert",
                    "consecutive_failures": new_failures,
                    "threshold": self.OPENCLAW_HEALTH_FAIL_THRESHOLD,
                    "alert_ts": ts_now,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("openclaw_health_alert_inbox_upsert_failed", error=str(exc))

        # Telegram alert с debounce
        if notifier is not None and debounce_ok:
            try:
                await notifier(
                    f"🚨 **OpenClaw Gateway недоступен**\n"
                    f"Не отвечает `http://127.0.0.1:18789` — {new_failures} проб подряд.\n"
                    "Используй `openclaw gateway` для рестарта (НЕ SIGHUP).\n"
                    "Проверь: `!health` или Owner Panel."
                )
                alerted = True
                state["openclaw_health_last_alert_ts"] = now_ts
                state["openclaw_health_alert_active"] = True
                self._save_state(state)
                logger.warning(
                    "openclaw_gateway_unreachable_alert_sent",
                    consecutive_failures=new_failures,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("openclaw_health_alert_send_failed", error=str(exc))
        elif not debounce_ok:
            logger.debug(
                "openclaw_health_alert_debounce_active",
                elapsed_sec=round(debounce_elapsed),
                debounce_sec=self.OPENCLAW_HEALTH_ALERT_DEBOUNCE_SEC,
            )

        return {"ok": False, "consecutive_failures": new_failures, "alerted": alerted}

    async def _openclaw_health_alert_loop(
        self,
        notifier: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        Бесконечный цикл мониторинга OpenClaw gateway.

        Проверяет gateway каждые OPENCLAW_HEALTH_CHECK_INTERVAL_SEC секунд.
        При OPENCLAW_HEALTH_FAIL_THRESHOLD последовательных сбоях — отправляет
        Telegram alert и создаёт inbox item.
        При восстановлении — recovery уведомление.

        Gate: KRAB_OPENCLAW_HEALTH_ALERT_ENABLED (default "1").
        Debounce: max 1 alert per OPENCLAW_HEALTH_ALERT_DEBOUNCE_SEC (30 min).
        """
        if not self._is_openclaw_health_alert_enabled():
            logger.info("openclaw_health_alert_disabled_by_gate")
            return

        # Mutable счётчик последовательных сбоев — передаём через list для mutability
        consecutive_failures: list[int] = [0]

        logger.info(
            "openclaw_health_alert_loop_started",
            interval_sec=self.OPENCLAW_HEALTH_CHECK_INTERVAL_SEC,
            fail_threshold=self.OPENCLAW_HEALTH_FAIL_THRESHOLD,
            debounce_sec=self.OPENCLAW_HEALTH_ALERT_DEBOUNCE_SEC,
        )

        while True:
            await asyncio.sleep(self.OPENCLAW_HEALTH_CHECK_INTERVAL_SEC)
            try:
                await self._check_openclaw_gateway_unreachable(
                    notifier=notifier,
                    _consecutive_failures=consecutive_failures,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("openclaw_health_alert_loop_error", error=str(exc))

    def start_openclaw_health_alert_loop(
        self,
        notifier: Callable[[str], Awaitable[None]] | None = None,
    ) -> "asyncio.Task[None]":
        """Запускает фоновый loop мониторинга OpenClaw gateway и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(
            self._openclaw_health_alert_loop(notifier=notifier),
            name="krab_openclaw_health_alert",
        )
        logger.info(
            "openclaw_health_alert_loop_task_created",
            enabled=self._is_openclaw_health_alert_enabled(),
        )
        return task

    # Cooldown между archive.db alert'ами (12 часов)
    ARCHIVE_DB_ALERT_COOLDOWN_SEC: int = 43200

    def _check_archive_db_size(self) -> bool:
        """
        Проверяет размер archive.db и создаёт inbox item при превышении порогов.

        Пороги (переопределяемые env-переменными):
        - ARCHIVE_DB_WARN_MB  (default 500) → severity=warning
        - ARCHIVE_DB_CRIT_MB  (default 1024) → severity=error

        Cooldown 12 часов между повторными alert'ами (дедупе по state_key).
        Возвращает True если алерт сработал.
        """
        db_path = Path(
            os.environ.get("ARCHIVE_DB_PATH", "~/.openclaw/krab_memory/archive.db")
        ).expanduser()
        if not db_path.exists():
            return False

        size_mb = db_path.stat().st_size / 1024 / 1024
        warn_threshold = float(os.environ.get("ARCHIVE_DB_WARN_MB", "500"))
        crit_threshold = float(os.environ.get("ARCHIVE_DB_CRIT_MB", "1024"))

        if size_mb < warn_threshold:
            return False

        # Cooldown: читаем persisted state чтобы не спамить
        state = self._load_state()
        last_alert_ts = float(state.get("archive_db_size_last_alert", 0))
        now = time.time()
        if now - last_alert_ts < self.ARCHIVE_DB_ALERT_COOLDOWN_SEC:
            return False

        # Определяем уровень
        is_critical = size_mb >= crit_threshold
        severity = "error" if is_critical else "warning"
        threshold_label = crit_threshold if is_critical else warn_threshold
        ts_now = _now_utc_iso()

        if is_critical:
            msg = (
                f"**Archive.db Critical** — {ts_now}\n"
                f"Размер archive.db: **{size_mb:.1f} MB** (критический порог: {crit_threshold:.0f} MB).\n"
                f"Рекомендуется: `scripts/maintenance_weekly.py --execute` "
                f"и очистка старых чатов."
            )
            title = f"Archive.db Critical: {size_mb:.1f} MB (>{crit_threshold:.0f} MB)"
        else:
            msg = (
                f"**Archive.db Warning** — {ts_now}\n"
                f"Размер archive.db: **{size_mb:.1f} MB** (порог предупреждения: {warn_threshold:.0f} MB).\n"
                f"Рекомендуется: `scripts/maintenance_weekly.py --execute`."
            )
            title = f"Archive.db Warning: {size_mb:.1f} MB (>{warn_threshold:.0f} MB)"

        inbox_service.upsert_item(
            dedupe_key=f"proactive:alert:archive_db_size:{ts_now[:13]}",
            kind="proactive_action",
            source="krab-internal",
            title=title,
            body=msg,
            severity=severity,
            status="open",
            identity=inbox_service.build_identity(
                channel_id="system",
                team_id="owner",
                trace_id="alert:archive_db_size",
                approval_scope="owner",
            ),
            metadata={
                "action_type": "archive_db_size_alert",
                "size_mb": round(size_mb, 2),
                "warn_threshold_mb": warn_threshold,
                "crit_threshold_mb": crit_threshold,
                "is_critical": is_critical,
                "alert_ts": ts_now,
            },
        )

        # Сохраняем timestamp последнего алерта в persisted state
        state["archive_db_size_last_alert"] = now
        self._save_state(state)

        logger.warning(
            "alert_archive_db_size_triggered",
            size_mb=round(size_mb, 1),
            threshold_mb=threshold_label,
            is_critical=is_critical,
        )
        return True

    # -----------------------------------------------------------------
    # Anomaly detection wire-up (Idea 26)
    # -----------------------------------------------------------------
    # Per-metric cooldown между alert'ами (6 часов) — без этого один и тот же
    # spike будет триггериться на каждой итерации loop'а до выхода точки из окна.
    ANOMALY_ALERT_COOLDOWN_SEC: int = 21600
    # Интервал anomaly-проверок: ~60s, как и заявлено в спеке.
    ANOMALY_CHECKS_INTERVAL_SEC: int = 60

    @staticmethod
    def _anomaly_cooldown_path() -> Path:
        """Путь к persisted-state cooldown'ов anomaly-алёртов."""
        return Path.home() / ".openclaw" / "krab_runtime_state" / "anomaly_alert_cooldowns.json"

    @staticmethod
    def _is_anomaly_detection_enabled() -> bool:
        """Глобальный gate: KRAB_ANOMALY_DETECTION_ENABLED, default off."""
        raw = os.environ.get("KRAB_ANOMALY_DETECTION_ENABLED", "0").strip().lower()
        return raw in ("1", "true", "yes", "on")

    def _load_anomaly_cooldowns(self) -> dict[str, float]:
        """Читает per-metric cooldowns. Падать не должны: corrupt → пустой dict."""
        path = self._anomaly_cooldown_path()
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as exc:
            logger.warning(
                "anomaly_cooldowns_read_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return {}
        if not isinstance(raw, dict):
            return {}
        # Filter обратно к dict[str, float] — corrupt entries молча выкидываем.
        result: dict[str, float] = {}
        for k, v in raw.items():
            try:
                result[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return result

    def _save_anomaly_cooldowns(self, payload: dict[str, float]) -> None:
        """Persist per-metric cooldowns. Тихо логируем сбой записи."""
        path = self._anomaly_cooldown_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except (OSError, TypeError) as exc:
            logger.warning(
                "anomaly_cooldowns_write_failed",
                path=str(path),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _collect_anomaly_metrics(self) -> dict[str, float]:
        """
        Собирает 5 целевых метрик для anomaly_detector.

        Все источники защищены try/except: если конкретный модуль недоступен,
        метрика просто пропускается, остальные пишутся.
        """
        result: dict[str, float] = {}

        # 1) response_time_p95 — observability latency p95 (ms).
        try:
            snap = _observability_metrics.get_snapshot()
            p95 = float(snap.get("latencies", {}).get("p95_ms") or 0.0)
            result["response_time_p95"] = p95
        except Exception as exc:  # noqa: BLE001
            logger.debug("anomaly_collect_p95_failed", error=str(exc))

        # 2) error_rate — % failed LLM calls / total (0..100).
        try:
            counters = _observability_metrics.get_snapshot().get("counters", {})
            errors = float(counters.get("llm_error", 0))
            success = float(counters.get("llm_success", 0))
            total = errors + success
            if total > 0:
                result["error_rate"] = (errors / total) * 100.0
        except Exception as exc:  # noqa: BLE001
            logger.debug("anomaly_collect_error_rate_failed", error=str(exc))

        # 3) inbox_open_count — текущее число open-items.
        try:
            open_items = inbox_service.list_items(status="open", limit=1000)
            result["inbox_open_count"] = float(len(open_items))
        except Exception as exc:  # noqa: BLE001
            logger.debug("anomaly_collect_inbox_open_failed", error=str(exc))

        # 4) memory_indexer_queue_size — глубина очереди индексатора.
        try:
            from .memory_indexer_worker import (  # noqa: PLC0415
                _worker_singleton as _indexer_singleton,
            )

            if _indexer_singleton is not None:
                stats = _indexer_singleton.get_stats()
                result["memory_indexer_queue_size"] = float(stats.queue_size)
        except Exception as exc:  # noqa: BLE001
            logger.debug("anomaly_collect_indexer_failed", error=str(exc))

        # 5) chat_filter_silence_ratio — доля чатов в режиме silence/mute (0..1).
        try:
            from .chat_filter_config import chat_filter_config  # noqa: PLC0415

            stats = chat_filter_config.stats()
            by_mode = stats.get("by_mode") or {}
            total_chats = sum(int(v) for v in by_mode.values())
            if total_chats > 0:
                silenced = int(by_mode.get("mute", 0)) + int(by_mode.get("silence", 0))
                result["chat_filter_silence_ratio"] = silenced / total_chats
        except Exception as exc:  # noqa: BLE001
            logger.debug("anomaly_collect_silence_ratio_failed", error=str(exc))

        return result

    async def run_anomaly_checks(self) -> dict[str, Any]:
        """
        Записывает 5 ключевых метрик в anomaly_detector и поднимает alert
        при detected anomaly (с per-metric cooldown).

        Gate: `KRAB_ANOMALY_DETECTION_ENABLED` (default off).
        """
        if not self._is_anomaly_detection_enabled():
            return {"enabled": False, "recorded": 0, "alerts": []}

        recorded = self._collect_anomaly_metrics()
        for metric, value in recorded.items():
            try:
                anomaly_detector.record_metric(metric, value)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "anomaly_record_failed",
                    metric=metric,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        try:
            anomalies: list[Anomaly] = anomaly_detector.detect_anomalies()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "anomaly_detect_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            anomalies = []

        cooldowns = self._load_anomaly_cooldowns()
        now_ts = time.time()
        alerted: list[dict[str, Any]] = []
        cooldowns_dirty = False

        for anomaly in anomalies:
            # Записываем alert только для метрик из нашего scope —
            # detector может содержать данные из других источников.
            if anomaly.metric not in recorded:
                continue
            last_alert = float(cooldowns.get(anomaly.metric, 0.0))
            if now_ts - last_alert < self.ANOMALY_ALERT_COOLDOWN_SEC:
                continue
            logger.warning(
                "proactive_anomaly_detected",
                metric=anomaly.metric,
                severity=anomaly.severity,
                z_score=round(anomaly.z_score, 3),
                current_value=anomaly.current_value,
                baseline_value=round(anomaly.baseline_value, 4),
                std_dev=round(anomaly.std_dev, 4),
                sample_count=anomaly.sample_count,
            )
            cooldowns[anomaly.metric] = now_ts
            cooldowns_dirty = True
            alerted.append(
                {
                    "metric": anomaly.metric,
                    "severity": anomaly.severity,
                    "z_score": anomaly.z_score,
                    "current_value": anomaly.current_value,
                    "baseline_value": anomaly.baseline_value,
                }
            )

        if cooldowns_dirty:
            self._save_anomaly_cooldowns(cooldowns)

        return {
            "enabled": True,
            "recorded": len(recorded),
            "metrics": recorded,
            "alerts": alerted,
        }

    async def _anomaly_checks_loop(self) -> None:
        """Бесконечный цикл: каждые ANOMALY_CHECKS_INTERVAL_SEC прогоняет проверку."""
        while True:
            await asyncio.sleep(self.ANOMALY_CHECKS_INTERVAL_SEC)
            try:
                await self.run_anomaly_checks()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "anomaly_checks_loop_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

    def start_anomaly_checks_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу anomaly-проверок и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._anomaly_checks_loop(), name="krab_anomaly_checks")
        logger.info(
            "anomaly_checks_loop_started",
            interval_sec=self.ANOMALY_CHECKS_INTERVAL_SEC,
            enabled=self._is_anomaly_detection_enabled(),
        )
        return task

    async def _run_alert_checks_loop(self) -> None:
        """Бесконечный цикл: каждые 30 минут запускает run_alert_checks."""
        while True:
            await asyncio.sleep(self.ALERT_CHECKS_INTERVAL_SEC)
            try:
                await self.run_alert_checks()
            except Exception as exc:  # noqa: BLE001
                logger.warning("alert_checks_loop_error", error=str(exc))

    def start_alert_checks_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу проверки alert-условий и возвращает Task."""
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._run_alert_checks_loop(), name="krab_alert_checks")
        logger.info("alert_checks_loop_started", interval_sec=self.ALERT_CHECKS_INTERVAL_SEC)
        return task

    def start_weekly_digest_loop(self) -> "asyncio.Task[None]":
        """Запускает фоновую задачу Weekly Digest (каждые 7 дней) и возвращает Task."""
        from .weekly_digest import weekly_digest  # ленивый импорт во избежание циклов

        loop = asyncio.get_event_loop()
        task = loop.create_task(weekly_digest._weekly_digest_loop(), name="krab_weekly_digest")
        logger.info("weekly_digest_loop_started", interval_sec=weekly_digest.INTERVAL_SEC)
        return task


proactive_watch = ProactiveWatchService()

__all__ = ["ProactiveWatchSnapshot", "ProactiveWatchService", "proactive_watch"]
