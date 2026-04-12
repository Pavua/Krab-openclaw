# -*- coding: utf-8 -*-
"""
Background-tasks mixin для `KraabUserbot`.

Шестой шаг декомпозиции `src/userbot_bridge.py` (session 4+, 2026-04-09).
Содержит жизненный цикл per-chat background tasks: регистрацию, получение
активной задачи, stale-reaper, cancel, typing-action keep-alive,
delivery-action, inbox ack и логирование исключений fire-and-forget tasks.

Замечания:
- `self._chat_background_tasks`, `self._chat_background_task_started_at`,
  `self._chat_processing_locks` — instance-dicts, инициализируются в
  `KraabUserbot.__init__`, доступны через MRO. Методы mixin'а используют
  defensive `getattr(self, ...)` fallback на случай вызова до __init__.
- `config.USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC` — единственная
  зависимость от конфига (stale timeout).
- `inbox_service` — singleton, используется в `_mark_incoming_item_background_started`.

См. `docs/USERBOT_BRIDGE_SPLIT_PROPOSAL.md` для полной стратегии разбиения.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..config import config
from ..core.inbox_service import inbox_service
from ..core.logger import get_logger

logger = get_logger(__name__)


class BackgroundTasksMixin:
    """
    Жизненный цикл per-chat background tasks и вспомогательные утилиты.

    Mixin для `KraabUserbot`: регистрация/получение/cancel фоновых задач,
    reaper зависших tasks, typing keep-alive, delivery action, inbox ack.
    """

    async def _cancel_background_task(self, attr_name: str) -> None:
        """
        Аккуратно отменяет и дожидается завершения фоновой задачи.

        Почему это важно:
        - простой `cancel()` не гарантирует, что задача уже отпустила Pyrogram transport;
        - restart без await создаёт окно для гонки между старым probe и новым lifecycle;
        - после await ссылка обнуляется, чтобы runtime не путал старую задачу с живой.
        """
        task = getattr(self, attr_name, None)
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("background_task_cancel_failed", task=attr_name, error=str(exc))
        finally:
            setattr(self, attr_name, None)

    def _get_chat_processing_lock(self, chat_id: str) -> asyncio.Lock:
        """
        Возвращает lock на конкретный чат.

        Почему это нужно:
        - без сериализации несколько сообщений из одного Telegram-чата могут
          одновременно зайти в LLM/TTS;
        - в voice-режиме это даёт наложение озвучки, гонки редактирования и
          ситуацию, когда текст/голос приезжают в перепутанном порядке;
        - per-chat lock убирает гонку локально, не запрещая параллельную
          обработку разных чатов.
        """
        chat_key = str(chat_id or "").strip() or "unknown"
        locks = getattr(self, "_chat_processing_locks", None)
        if locks is None:
            locks = {}
            self._chat_processing_locks = locks
        lock = locks.get(chat_key)
        if lock is None:
            lock = asyncio.Lock()
            locks[chat_key] = lock
        return lock

    @staticmethod
    async def _keep_typing_alive(
        client: Any, chat_id: int, action: Any, stop_event: asyncio.Event
    ) -> None:
        """Фоновая корутина: повторяет send_chat_action каждые 4 секунды, пока не установлен stop_event."""
        while not stop_event.is_set():
            try:
                await client.send_chat_action(chat_id, action)
            except Exception:
                pass
            try:
                await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=4.0)
            except asyncio.TimeoutError:
                pass

    @staticmethod
    async def _send_delivery_chat_action(client: Any, chat_id: int, action: Any) -> None:
        """
        Отправляет одноразовый delivery-action перед реальной отправкой вложения.

        Во время reasoning/tool-flow пользователю полезнее видеть `typing`.
        Отдельный upload-action посылаем только в финале, когда голос или
        документ уже действительно готов к отправке.
        """
        try:
            await client.send_chat_action(chat_id, action)
        except Exception:
            pass

    def _mark_incoming_item_background_started(
        self,
        *,
        incoming_item_result: dict[str, Any] | None,
        note: str = "background_processing_started",
    ) -> dict[str, Any]:
        """Переводит входящий inbox item в `acked`, если у него уже есть persisted запись."""
        if not isinstance(incoming_item_result, dict) or not incoming_item_result.get("ok"):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        item = incoming_item_result.get("item")
        if not isinstance(item, dict):
            return {"ok": False, "skipped": True, "reason": "incoming_item_missing"}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        chat_id = str((metadata or {}).get("chat_id") or "").strip()
        message_id = str((metadata or {}).get("message_id") or "").strip()
        if not chat_id or not message_id:
            return {"ok": False, "skipped": True, "reason": "incoming_item_identity_incomplete"}
        return inbox_service.set_status_by_dedupe(
            f"incoming:{chat_id}:{message_id}",
            status="acked",
            actor="kraab",
            note=note,
            event_action="background_started",
        )

    @staticmethod
    def _log_background_task_exception_cb(task: asyncio.Task) -> None:
        """
        done_callback для fire-and-forget `asyncio.create_task(...)` — вытаскивает
        исключение через `task.exception()` и логгирует его.

        Причина: без done_callback необработанные exceptions внутри fire-and-forget
        корутин превращаются в `Task exception was never retrieved` warning от
        asyncio, с потерей stack trace. Хуже: если у task нет сильной ссылки
        в caller'е, GC может прибить task до завершения. Этот callback
        эффективно consumer'ит exception и даёт нам structured log вместо
        тихого молчания. Используется из `_refresh_chat_capabilities_background`
        и других fire-and-forget call sites.
        """
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except Exception:  # noqa: BLE001
            return
        if exc is not None:
            logger.warning(
                "background_task_exception",
                task_name=getattr(task, "get_name", lambda: "unknown")(),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _register_chat_background_task(self, chat_id: str, task: asyncio.Task) -> None:
        """Регистрирует background-task чата и автоматически чистит stale-ссылку после завершения."""
        tasks = getattr(self, "_chat_background_tasks", None)
        if tasks is None:
            tasks = {}
            self._chat_background_tasks = tasks
        started_at_rows = getattr(self, "_chat_background_task_started_at", None)
        if started_at_rows is None:
            started_at_rows = {}
            self._chat_background_task_started_at = started_at_rows
        chat_key = str(chat_id or "").strip() or "unknown"
        task_started_at = time.monotonic()
        tasks[chat_key] = task
        started_at_rows[chat_key] = task_started_at

        def _cleanup(_task: asyncio.Task) -> None:
            current = tasks.get(chat_key)
            if current is _task:
                tasks.pop(chat_key, None)
            current_started_at = started_at_rows.get(chat_key)
            if current_started_at == task_started_at:
                started_at_rows.pop(chat_key, None)

        task.add_done_callback(_cleanup)

    def _get_active_chat_background_task(self, chat_id: str) -> asyncio.Task | None:
        """Возвращает активную background-task чата, если она ещё жива."""
        tasks = getattr(self, "_chat_background_tasks", None) or {}
        started_at_rows = getattr(self, "_chat_background_task_started_at", None) or {}
        chat_key = str(chat_id or "").strip() or "unknown"
        task = tasks.get(chat_key)
        if task is None:
            started_at_rows.pop(chat_key, None)
            return None
        if task.done():
            tasks.pop(chat_key, None)
            started_at_rows.pop(chat_key, None)
            return None
        stale_timeout_sec = max(
            60.0,
            float(getattr(config, "USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC", 900.0) or 900.0),
        )
        started_at = float(started_at_rows.get(chat_key) or 0.0)
        if started_at > 0.0:
            age_sec = max(0.0, time.monotonic() - started_at)
            if age_sec > stale_timeout_sec:
                logger.warning(
                    "chat_background_task_stale_cancelled",
                    chat_id=chat_key,
                    age_sec=round(age_sec, 3),
                    stale_timeout_sec=stale_timeout_sec,
                )
                task.cancel()
                tasks.pop(chat_key, None)
                started_at_rows.pop(chat_key, None)
                return None
        return task
        return None

    async def _background_task_reaper(self) -> None:
        """Периодически отменяет зависшие background tasks без ожидания нового сообщения."""
        reaper_interval = 60.0
        while True:
            try:
                await asyncio.sleep(reaper_interval)
                tasks = getattr(self, "_chat_background_tasks", None) or {}
                started_at_rows = getattr(self, "_chat_background_task_started_at", None) or {}
                if not tasks:
                    continue
                stale_timeout_sec = max(
                    60.0,
                    float(
                        getattr(config, "USERBOT_BACKGROUND_TASK_STALE_TIMEOUT_SEC", 900.0) or 900.0
                    ),
                )
                now = time.monotonic()
                stale_keys = []
                for chat_key, task in list(tasks.items()):
                    if task.done():
                        continue
                    started_at = float(started_at_rows.get(chat_key) or 0.0)
                    if started_at <= 0.0:
                        continue
                    age_sec = now - started_at
                    if age_sec > stale_timeout_sec:
                        stale_keys.append((chat_key, age_sec, task))
                for chat_key, age_sec, task in stale_keys:
                    logger.warning(
                        "background_task_reaper_cancelled",
                        chat_id=chat_key,
                        age_sec=round(age_sec, 1),
                        stale_timeout_sec=stale_timeout_sec,
                    )
                    task.cancel()
                    tasks.pop(chat_key, None)
                    started_at_rows.pop(chat_key, None)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("background_task_reaper_error", error=repr(exc))
