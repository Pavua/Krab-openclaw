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
from .auto_restart_policy import (
    RESTART_COMMANDS,
    auto_restart_manager,
    is_auto_restart_enabled,
)
from .inbox_service import inbox_service
from .logger import get_logger
from .openclaw_runtime_models import get_runtime_primary_model
from .openclaw_workspace import append_workspace_memory_entry
from .scheduler import krab_scheduler
from .subprocess_env import clean_subprocess_env

# Порог «критических» ошибок для alert inbox_critical
_INBOX_CRITICAL_ERROR_THRESHOLD: int = 5

# Коэффициент «зависания» swarm job: если не запускалась дольше interval * N — алерт
_SWARM_STALL_FACTOR: float = 2.0

logger = get_logger(__name__)


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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ProactiveWatchSnapshot":
        """Восстанавливает snapshot из persisted state."""
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
                macos_status = await macos_automation.status(apps_limit=2)
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_macos_status_failed", error=str(exc))
                macos_status = {}

        gateway_ok = await openclaw_client.health_check()
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

            updated[job_id] = {"last_run_at_ms": last_run_at_ms, "last_status": last_status}

            prev_run_at = int((last_cron_runs.get(job_id) or {}).get("last_run_at_ms") or 0)
            if last_run_at_ms <= 0 or last_run_at_ms == prev_run_at:
                continue  # нет нового выполнения

            severity = "warning" if last_status in ("error", "failed", "failure") else "info"
            run_ts = datetime.fromtimestamp(last_run_at_ms / 1000, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
            try:
                inbox_service.upsert_item(
                    dedupe_key=f"proactive:cron_run:{job_id}:{last_run_at_ms}",
                    kind="proactive_action",
                    source="krab-internal",
                    title=f"Cron job выполнен: {job_name}",
                    body=(
                        f"Job `{job_name}` (id={job_id}) выполнен в {run_ts}. "
                        f"Статус: `{last_status}`."
                    ),
                    severity=severity,
                    status="open",
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

        if persist_memory and (manual or bool(reason)):
            wrote_memory = append_workspace_memory_entry(
                self.render_memory_entry(snapshot, reason=reason, manual=manual),
                source="proactive-watch",
            )

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

        if notify and reason and notifier is not None and cooldown_ok:
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
            "enabled": bool(getattr(config, "PROACTIVE_WATCH_ENABLED", False)),
            "interval_sec": int(getattr(config, "PROACTIVE_WATCH_INTERVAL_SEC", 900) or 900),
            "alert_cooldown_sec": self.alert_cooldown_sec,
            "last_reason": str(state.get("last_reason") or ""),
            "last_digest_ts": str(state.get("last_digest_ts") or ""),
            "last_alert_ts": str(state.get("last_alert_ts") or ""),
            "last_alerted_reason": str(state.get("last_alerted_reason") or ""),
            "last_snapshot": snapshot,
        }

    # Интервал Error Digest в секундах (6 часов)
    ERROR_DIGEST_INTERVAL_SEC: int = 21600
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
            return {"ok": False, "error": str(exc)}

        logger.info("error_digest_written", total=total, counts=counts)
        return {"ok": True, "total": total, "counts": counts, "digest_ts": ts_now}

    async def _error_digest_loop(self) -> None:
        """Бесконечный цикл: каждые 6 часов запускает run_error_digest."""
        while True:
            await asyncio.sleep(self.ERROR_DIGEST_INTERVAL_SEC)
            try:
                await self.run_error_digest()
            except Exception as exc:  # noqa: BLE001
                logger.warning("error_digest_loop_error", error=str(exc))

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
