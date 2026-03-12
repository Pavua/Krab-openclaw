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
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from ..config import config
from ..integrations.macos_automation import macos_automation
from ..memory_engine import memory_manager
from ..model_manager import model_manager
from ..openclaw_client import openclaw_client
from .logger import get_logger
from .openclaw_runtime_models import get_runtime_primary_model
from .openclaw_workspace import append_workspace_memory_entry
from .scheduler import krab_scheduler


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
            60,
            int(alert_cooldown_sec or getattr(config, "PROACTIVE_WATCH_ALERT_COOLDOWN_SEC", 1800) or 1800),
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
                logger.warning("proactive_watch_state_read_failed", path=str(candidate), error=str(exc))
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
        if previous.macos_frontmost_app != current.macos_frontmost_app and current.macos_frontmost_app:
            return "frontmost_app_changed"
        return ""

    @staticmethod
    def render_digest(snapshot: ProactiveWatchSnapshot, *, reason: str, manual: bool) -> str:
        """Формирует человекочитаемый owner-digest для Telegram/UI."""
        label = reason or ("manual_snapshot" if manual else "baseline")
        route_model = snapshot.route_model or snapshot.primary_model or "n/a"
        route_provider = snapshot.route_provider or "n/a"
        front_app = snapshot.macos_frontmost_app or "n/a"
        scheduler_line = (
            f"{snapshot.scheduler_pending} pending"
            + (f", next `{snapshot.scheduler_next_due_at}`" if snapshot.scheduler_next_due_at else "")
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
            + (
                f" / `{snapshot.macos_frontmost_window}`"
                if snapshot.macos_frontmost_window
                else ""
            )
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

        if persist_memory and (manual or bool(reason)):
            wrote_memory = append_workspace_memory_entry(
                self.render_memory_entry(snapshot, reason=reason, manual=manual),
                source="proactive-watch",
            )

        last_alert_ts = str(state.get("last_alert_ts") or "")
        cooldown_ok = True
        if last_alert_ts:
            try:
                elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last_alert_ts)
                cooldown_ok = elapsed.total_seconds() >= self.alert_cooldown_sec
            except ValueError:
                cooldown_ok = True

        if notify and reason and notifier is not None and cooldown_ok:
            try:
                await notifier(digest)
                alerted = True
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_watch_notify_failed", reason=reason, error=str(exc))

        payload = {
            "last_snapshot": asdict(snapshot),
            "last_reason": reason,
            "last_digest_ts": snapshot.ts_utc if (manual or reason) else str(state.get("last_digest_ts") or ""),
            "last_alert_ts": snapshot.ts_utc if alerted else last_alert_ts,
            "last_alerted_reason": reason if alerted else str(state.get("last_alerted_reason") or ""),
        }
        self._save_state(payload)
        return {
            "snapshot": asdict(snapshot),
            "reason": reason,
            "digest": digest,
            "alerted": alerted,
            "wrote_memory": wrote_memory,
            "manual": manual,
            "baseline_created": previous is None,
        }

    def get_status(self) -> dict[str, Any]:
        """Возвращает persisted статус watch-контура для команд/UI."""
        state = self._load_state()
        snapshot = state.get("last_snapshot") if isinstance(state.get("last_snapshot"), dict) else {}
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


proactive_watch = ProactiveWatchService()

__all__ = ["ProactiveWatchSnapshot", "ProactiveWatchService", "proactive_watch"]
