# -*- coding: utf-8 -*-
"""Wave 31-L: ServiceOrchestrationMixin — ensure-start + scheduler binding.

Зачем:
- bridge до 31-L содержал ~4721 LOC, ensure-start + scheduler-bind cluster ~135 LOC,
  cohesive: все методы lifecycle вторичных runtime-сервисов (maintenance,
  silence schedule, memory indexer, krab/swarm/cron schedulers).
- Mixin использует: ``self.client``, ``self.me``, ``self.maintenance_task``,
  ``self._silence_schedule_task``, ``self._memory_indexer_task``,
  ``self._send_scheduled_message`` (CronTaskMixin), ``self._run_cron_prompt_and_send``
  (CronTaskMixin), ``self._build_system_prompt_for_sender`` (LLMFlowMixin),
  ``self._safe_maintenance`` (bridge), ``self._log_background_task_exception_cb``
  (BackgroundTasksMixin).

Контракт:
- ``_ensure_maintenance_started`` — idempotent boot model_manager maintenance task
- ``_ensure_silence_schedule_started`` — idempotent boot night-mode silence loop
- ``_ensure_memory_indexer_started`` — lazy boot Memory Indexer Worker (Phase 4)
- ``_sync_scheduler_runtime`` — bind sender + start всех schedulers (krab,
  swarm, swarm_auto_executor, cron_native, swarm_channels) при enabled+connected;
  иначе graceful stop.
- ``_ensure_audit_rotate_loop_started`` — Wave 56-I: daily 03:00 ротация audit-логов
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from ..config import config
from ..core.cron_native_scheduler import cron_native_scheduler
from ..core.memory_indexer_worker import get_indexer
from ..core.scheduler import krab_scheduler
from ..core.silence_mode import silence_manager
from ..core.silence_schedule import silence_schedule_manager
from ..core.state_snapshots import state_snapshot_manager
from ..core.swarm_auto_executor import swarm_auto_executor
from ..core.swarm_channels import swarm_channels
from ..core.swarm_scheduler import swarm_scheduler

if TYPE_CHECKING:
    from pyrogram import Client

logger = structlog.get_logger("Krab.userbot.service_orchestration")


class ServiceOrchestrationMixin:
    """Mixin: ensure-start + scheduler-bind для secondary runtime services."""

    # Атрибуты, которые ожидаются на host-классе:
    client: "Client | None"
    me: object | None
    maintenance_task: asyncio.Task | None
    _silence_schedule_task: asyncio.Task | None
    _memory_indexer_task: asyncio.Task | None
    # Wave 49-F: фоновый loop периодических snapshots state-файлов
    _state_snapshot_task: "asyncio.Task | None" = None
    # Wave 56-I: фоновый loop ежедневной ротации audit-логов
    _audit_rotate_task: "asyncio.Task | None" = None

    def _ensure_maintenance_started(self) -> None:
        """Запускает maintenance-задачу model_manager, если она еще не активна."""
        if self.maintenance_task and not self.maintenance_task.done():
            return
        self.maintenance_task = asyncio.create_task(self._safe_maintenance())

    def _ensure_silence_schedule_started(self) -> None:
        """Запускает фоновый loop проверки расписания ночного режима."""
        if self._silence_schedule_task and not self._silence_schedule_task.done():
            return

        def _apply_mute() -> None:
            silence_manager.mute_global(minutes=480)  # максимум 8 часов запас

        def _remove_mute() -> None:
            silence_manager.unmute_global()

        self._silence_schedule_task = asyncio.create_task(
            silence_schedule_manager.run_loop(_apply_mute, _remove_mute)
        )

    def _ensure_state_snapshot_loop_started(self) -> None:
        """
        Wave 49-F: запускает фоновый loop периодических snapshots state-файлов.

        - На старте сразу делает snapshot_now(reason="startup").
        - Затем каждые KRAB_STATE_SNAPSHOT_INTERVAL_MINUTES (default 60) — scheduled.
        - Cleanup (retention) применяется раз в сутки.
        """
        existing = getattr(self, "_state_snapshot_task", None)
        if existing and not existing.done():
            return

        async def _runner() -> None:
            # Startup snapshot — даже если loop потом упадёт, у нас есть свежий backup.
            try:
                state_snapshot_manager.snapshot_now(reason="startup")
            except Exception as exc:  # noqa: BLE001
                logger.warning("state_snapshot_startup_failed", error=str(exc))

            last_cleanup_ts = 0.0
            cleanup_interval_sec = 86400.0  # раз в сутки
            while True:
                try:
                    interval_min = state_snapshot_manager.interval_minutes
                    await asyncio.sleep(max(60, interval_min * 60))
                    state_snapshot_manager.snapshot_now(reason="scheduled")
                    # Cleanup раз в сутки.
                    loop = asyncio.get_running_loop()
                    now_mono = loop.time()
                    if now_mono - last_cleanup_ts >= cleanup_interval_sec:
                        state_snapshot_manager.cleanup_old()
                        last_cleanup_ts = now_mono
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("state_snapshot_loop_iteration_failed", error=str(exc))

        try:
            self._state_snapshot_task = asyncio.create_task(_runner())
            logger.info(
                "state_snapshot_loop_started",
                interval_minutes=state_snapshot_manager.interval_minutes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("state_snapshot_loop_start_failed", error=str(exc), non_fatal=True)

    def _ensure_audit_rotate_loop_started(self) -> None:
        """Wave 56-I: запускает фоновый loop ежедневной ротации audit-логов в 03:00 local.

        Параметры через env:
          KRAB_AUDIT_LOG_MAX_MB (default 10) — порог ротации, МБ
          KRAB_AUDIT_LOG_KEEP   (default 5)  — количество хранимых gzip-архивов
        """
        existing = getattr(self, "_audit_rotate_task", None)
        if existing and not existing.done():
            return

        import os  # noqa: PLC0415

        max_mb = int(os.environ.get("KRAB_AUDIT_LOG_MAX_MB", "10"))
        keep = int(os.environ.get("KRAB_AUDIT_LOG_KEEP", "5"))

        async def _runner() -> None:
            from datetime import datetime  # noqa: PLC0415

            from ..core.audit_log_rotator import audit_log_rotator  # noqa: PLC0415

            while True:
                try:
                    # Вычислить секунды до следующего 03:00 local
                    now = datetime.now()
                    target = now.replace(hour=3, minute=0, second=0, microsecond=0)
                    if target <= now:
                        # Уже прошло сегодня — ждать до завтра
                        from datetime import timedelta  # noqa: PLC0415

                        target += timedelta(days=1)
                    delay = (target - now).total_seconds()
                    await asyncio.sleep(delay)
                    # Ротация обоих логов
                    results = audit_log_rotator.rotate_all(max_size_mb=max_mb, keep_count=keep)
                    logger.info(
                        "audit_rotate_daily_done",
                        bash=results.get("bash", {}).get("rotated"),
                        agent=results.get("agent", {}).get("rotated"),
                    )
                except asyncio.CancelledError:
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning("audit_rotate_loop_error", error=str(exc))
                    await asyncio.sleep(3600)  # Подождать час при ошибке

        try:
            self._audit_rotate_task = asyncio.create_task(_runner())
            logger.info("audit_rotate_loop_started", max_mb=max_mb, keep=keep)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_rotate_loop_start_failed", error=str(exc), non_fatal=True)

    def _ensure_memory_indexer_started(self) -> None:
        """Lazy boot Memory Indexer Worker (Phase 4)."""
        if self._memory_indexer_task and not self._memory_indexer_task.done():
            return
        try:
            indexer = get_indexer()
            self._memory_indexer_task = asyncio.create_task(indexer.start())
            self._memory_indexer_task.add_done_callback(self._log_background_task_exception_cb)
            logger.info("memory_indexer_supervisor_started")
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_indexer_start_failed", error=str(exc), non_fatal=True)

    def _sync_scheduler_runtime(self) -> None:
        """
        Синхронизирует состояние scheduler с runtime:
        - при enabled + connected: bind sender и старт;
        - иначе: безопасная остановка.
        """
        scheduler_enabled = bool(getattr(config, "SCHEDULER_ENABLED", False))
        client_connected = bool(self.client and self.client.is_connected)

        if scheduler_enabled and client_connected:
            krab_scheduler.bind_sender(self._send_scheduled_message)
            # Перенаправлять напоминания из групп в DM владельца
            if self.me:
                krab_scheduler.bind_owner_chat_id(str(self.me.id))
            if not krab_scheduler.is_started:
                krab_scheduler.start()
                logger.info("scheduler_runtime_started")

            # Swarm scheduler — рекуррентные автономные прогоны
            if config.SWARM_AUTONOMOUS_ENABLED and self.me:
                owner_chat_id = str(self.me.id)
                system_prompt = self._build_system_prompt_for_sender(
                    is_allowed_sender=True,
                    access_level="owner",
                )

                def _swarm_router_factory(team_name: str):
                    from ..handlers.command_handlers import (  # noqa: PLC0415
                        _AgentRoomRouterAdapter,
                    )

                    return _AgentRoomRouterAdapter(
                        chat_id=f"swarm:scheduled:{team_name}",
                        system_prompt=system_prompt,
                        team_name=team_name,
                    )

                swarm_scheduler.bind(
                    sender=self._send_scheduled_message,
                    router_factory=_swarm_router_factory,
                    owner_chat_id=owner_chat_id,
                )
                if not swarm_scheduler._started:
                    swarm_scheduler.start()
                    logger.info("swarm_scheduler_runtime_started")

            # Swarm auto-executor — авто-выполнение задач board с auto_execute=True
            if config.KRAB_SWARM_AUTO_EXECUTE_ENABLED and self.me:
                _auto_owner_chat_id = str(self.me.id)
                _auto_system_prompt = self._build_system_prompt_for_sender(
                    is_allowed_sender=True,
                    access_level="owner",
                )

                def _auto_executor_router_factory(team_name: str):
                    from ..handlers.command_handlers import (  # noqa: PLC0415
                        _AgentRoomRouterAdapter,
                    )

                    return _AgentRoomRouterAdapter(
                        chat_id=f"swarm:auto:{team_name}",
                        system_prompt=_auto_system_prompt,
                        team_name=team_name,
                    )

                swarm_auto_executor.bind(
                    sender=self._send_scheduled_message,
                    router_factory=_auto_executor_router_factory,
                    owner_chat_id=_auto_owner_chat_id,
                )
                if not swarm_auto_executor._started:
                    swarm_auto_executor.start()
                    logger.info("swarm_auto_executor_runtime_started")

            # Native cron scheduler — fallback когда OpenClaw CLI недоступен
            cron_native_scheduler.bind_sender(self._run_cron_prompt_and_send)
            if not cron_native_scheduler.is_running:
                cron_native_scheduler.start()
                logger.info("cron_native_scheduler_runtime_started")

            # Wave 49-F: периодические snapshots критичных state-файлов
            self._ensure_state_snapshot_loop_started()

            # Wave 56-I: ежедневная ротация audit-логов в 03:00
            self._ensure_audit_rotate_loop_started()

            # Swarm channels — live broadcast в Telegram-группы
            if self.me and self.client:
                swarm_channels.bind(client=self.client, owner_id=self.me.id)
                logger.info("swarm_channels_bound", teams=list(swarm_channels.get_all_team_chats()))
            return

        if krab_scheduler.is_started:
            krab_scheduler.stop()
            logger.info(
                "scheduler_runtime_stopped",
                scheduler_enabled=scheduler_enabled,
                client_connected=client_connected,
            )

        # Wave 49-F: shutdown snapshot + остановка loop при graceful shutdown
        snap_task = getattr(self, "_state_snapshot_task", None)
        if snap_task and not snap_task.done():
            try:
                state_snapshot_manager.snapshot_now(reason="shutdown")
            except Exception as exc:  # noqa: BLE001
                logger.warning("state_snapshot_shutdown_failed", error=str(exc))
            snap_task.cancel()
            self._state_snapshot_task = None
