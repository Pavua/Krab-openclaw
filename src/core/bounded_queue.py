# -*- coding: utf-8 -*-
"""
Bounded Request Queue с backpressure и приоритетами.

Роль модуля:
    Заменяет задний конец TaskQueue (unbounded dict + asyncio.create_task).
    Добавляет:
    1. Bounded capacity — очередь не растёт бесконечно при перегрузке.
    2. Backpressure   — при превышении лимита пользователь получает честное
                        сообщение вместо того, чтобы зависнуть в «🤔 Думаю...».
    3. Priority       — owner/private команды получают слот раньше обычных.
    4. Concurrency cap— не более max_running параллельных executions.

Приоритеты (меньше = выше):
    PRIORITY_OWNER   = 0  — владелец бота в личке
    PRIORITY_NORMAL  = 1  — обычные пользователи / группы

Использование в main.py / handlers:
    queue = BoundedRequestQueue(app, config)
    task_id = await queue.enqueue(
        name="chat",
        chat_id=msg.chat.id,
        coro=model_router.route_query(prompt),
        is_owner=is_owner,
    )
    if task_id is None:
        await msg.reply("⚠️ Очередь переполнена. Повтори через ~30 секунд.")

    # При завершении (например, при SIGTERM):
    await queue.shutdown()

Зачем эти числа (ADR_R24_routing_stability.md):
    - max_queue_size=20 : при 1 req/sec — 20с запаса; при burst — честный backpressure
    - max_running=5     : не перегружаем OpenClaw / LM Studio параллельными вызовами
    - SLA_TIMEOUT=35s   : совместимо с Telegram timeout (API отвечает до 30с)

R25-core патч (ADR-дополнение):
    Причина RuntimeWarning "coroutine was never awaited":
        asyncio.create_task() без сохранения результата в set — Python GC
        мог собрать задачу до её выполнения, что давало RuntimeWarning.
    Исправление:
        Все активные задачи хранятся в self._active_tasks (Set[asyncio.Task]).
        В callback done после завершения — задача удаляется из set.
        Метод shutdown() делает graceful cancel всех незавершённых задач.
    Причина "Event loop is closed":
        _running_sem создавался однократно и не сбрасывался при смене event loop
        (pytest-asyncio создаёт новый loop для каждого теста).
    Исправление:
        Семафор сбрасывается в None при вызове shutdown(), что поддерживает
        повторное использование объекта в разных тестовых loop'ах.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Coroutine, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Приоритеты задач
PRIORITY_OWNER = 0   # Владелец / private — выполняется раньше
PRIORITY_NORMAL = 1  # Остальные запросы


class QueueTask:
    """Метаданные одной задачи в очереди."""

    def __init__(self, task_id: str, name: str, chat_id: int) -> None:
        self.id = task_id
        self.name = name
        self.chat_id = chat_id
        self.status = "PENDING"   # PENDING | RUNNING | COMPLETED | FAILED | REJECTED
        self.start_time = datetime.now()
        self.end_time: Optional[datetime] = None
        self.result: Any = None
        self.error: Optional[str] = None
        self.priority: int = PRIORITY_NORMAL

    def __lt__(self, other: "QueueTask") -> bool:
        """Нужен для heapq / PriorityQueue сравнения."""
        return self.priority < other.priority


class BoundedRequestQueue:
    """
    Bounded priority queue для Telegram-запросов с backpressure.

    Совместим с интерфейсом TaskQueue (метод enqueue, get_metrics, list_active).
    Используется как drop-in замена для TaskQueue в main.py.

    Параметры из config:
        QUEUE_MAX_SIZE     — максимальная длина очереди ожидания (дефолт 20)
        QUEUE_MAX_RUNNING  — максимальный параллелизм (дефолт 5)
        QUEUE_SLA_TIMEOUT  — SLA таймаут на задачу в секундах (дефолт 35)
    """

    def __init__(self, app: Any, config: Dict[str, Any] | None = None) -> None:
        self.app = app  # Pyrogram Client для уведомлений
        cfg = config or {}

        try:
            self.max_queue_size = max(1, int(cfg.get("QUEUE_MAX_SIZE", 20)))
        except (ValueError, TypeError):
            self.max_queue_size = 20

        try:
            self.max_running = max(1, int(cfg.get("QUEUE_MAX_RUNNING", 5)))
        except (ValueError, TypeError):
            self.max_running = 5

        try:
            self.sla_timeout = max(0.01, float(cfg.get("QUEUE_SLA_TIMEOUT", 35)))
        except (ValueError, TypeError):
            self.sla_timeout = 35.0

        # Словарь задач (id → QueueTask) — для lookup
        self.tasks: Dict[str, QueueTask] = {}

        # Семафор параллелизма (ленивно создаётся при первом использовании).
        # Ленивная инициализация нужна, чтобы семафор создавался в правильном event loop
        # (pytest-asyncio создаёт свой loop для каждого теста).
        # R25-core: сбрасывается в None при shutdown() для поддержки смены loop.
        self._running_sem: asyncio.Semaphore | None = None

        # R25-core: Хранилище ссылок на активные asyncio.Task.
        # Без этого Python 3.12 GC может собрать задачу до завершения → RuntimeWarning.
        self._active_tasks: Set[asyncio.Task] = set()  # type: ignore[type-arg]

        # Флаг завершения — блокирует новые enqueue после shutdown()
        self._shutdown = False

        # Счётчики
        self._running_count = 0
        self._pending_count = 0
        self._total_enqueued = 0   # Всего поставлено в очередь (для backpressure)
        self._total_completed = 0
        self._total_failed = 0
        self._total_sla_aborts = 0
        self._total_rejected = 0      # Backpressure rejections
        self._total_execution_time = 0.0

    # ─────────────────────────────────────────────────────────────────── #
    # Публичный API (совместим с TaskQueue)
    # ─────────────────────────────────────────────────────────────────── #

    async def enqueue(
        self,
        name: str,
        chat_id: int,
        coro: Coroutine,
        *,
        is_owner: bool = False,
        priority: int | None = None,
    ) -> Optional[str]:
        """
        Добавляет задачу в очередь.

        Returns:
            task_id (str)  — если задача принята.
            None           — если очередь переполнена (backpressure) или shutdown.
        """
        # Если shutdown — не принимаем новые задачи, корутину закрываем сами
        if self._shutdown:
            coro.close()
            return None

        # Backpressure: проверяем лимит по суммарному кол-ву (pending + running)
        # Это гарантирует правильную работу даже при быстром захвате семафора
        total_active = self._pending_count + self._running_count
        if total_active >= self.max_queue_size:
            self._total_rejected += 1
            logger.warning(
                "Queue full — request rejected (backpressure): pending=%d running=%d max=%d",
                self._pending_count, self._running_count, self.max_queue_size,
            )
            # R25-core: явно закрываем переданную корутину при backpressure reject.
            # Python 3.12 даёт RuntimeWarning "coroutine was never awaited" если
            # coroutine создана вызывающим кодом, но не awaited нигде.
            # coro.close() безопасно — генерирует GeneratorExit без side effects.
            try:
                coro.close()
            except Exception:
                pass  # Игнорируем любые ошибки при закрытии
            return None


        task_id = str(uuid.uuid4())[:8]
        task_priority = priority if priority is not None else (
            PRIORITY_OWNER if is_owner else PRIORITY_NORMAL
        )
        bt = QueueTask(task_id, name, chat_id)
        bt.priority = task_priority
        self.tasks[task_id] = bt
        self._pending_count += 1
        self._total_enqueued += 1

        # R25-core: сохраняем ссылку на Task в _active_tasks.
        # Без этого Python 3.12 GC может уничтожить задачу до её выполнения,
        # что приводит к RuntimeWarning "coroutine was never awaited".
        task = asyncio.create_task(self._run_wrapper(bt, coro))
        self._active_tasks.add(task)
        # Callback для автоочистки при завершении — удаляет задачу из set
        task.add_done_callback(self._active_tasks.discard)

        logger.debug(
            "Task enqueued: id=%s name=%s priority=%d pending=%d",
            task_id, name, task_priority, self._pending_count,
        )
        return task_id

    async def shutdown(self, timeout: float = 5.0) -> None:
        """
        Graceful shutdown: отменяет все незавершённые задачи.

        R25-core: необходим для предотвращения "Event loop is closed" в тестах
        и при штатном завершении процесса (SIGTERM).

        Args:
            timeout: максимальное время ожидания завершения задач (секунды).
        """
        self._shutdown = True

        # Отменяем все активные задачи
        active = list(self._active_tasks)
        if not active:
            return

        logger.info(
            "BoundedRequestQueue shutdown: cancelling %d active tasks", len(active)
        )
        for task in active:
            task.cancel()

        # Ждём завершения (с общим таймаутом)
        try:
            await asyncio.wait_for(
                asyncio.gather(*active, return_exceptions=True),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "BoundedRequestQueue shutdown timeout: %d tasks may still be running",
                len([t for t in active if not t.done()]),
            )
        finally:
            # Сбрасываем семафор — подготовка к возможному переиспользованию
            self._running_sem = None
            self._active_tasks.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Метрики очереди для Dashboard (обратная совместимость с TaskQueue)."""
        avg_time = 0.0
        total_finished = self._total_completed + self._total_failed
        if total_finished > 0:
            avg_time = round(self._total_execution_time / total_finished, 2)

        return {
            "active_tasks": self._running_count,
            "waiting_tasks": self._pending_count,
            "completed_count": self._total_completed,
            "failed_count": self._total_failed,
            "sla_aborts": self._total_sla_aborts,
            "rejected_count": self._total_rejected,     # Новый: backpressure
            "avg_task_seconds": avg_time,
            "total_tasks_ever": total_finished + self._running_count,
            "max_queue_size": self.max_queue_size,
            "max_running": self.max_running,
        }

    def get_status(self, task_id: str) -> Optional[QueueTask]:
        """Возвращает задачу по id."""
        return self.tasks.get(task_id)

    def list_active(self) -> List[QueueTask]:
        """Список активных задач."""
        return [t for t in self.tasks.values() if t.status in ("PENDING", "RUNNING")]

    # ─────────────────────────────────────────────────────────────────── #
    # Внутренние методы
    # ─────────────────────────────────────────────────────────────────── #

    async def _run_wrapper(self, bt: QueueTask, coro: Coroutine) -> None:
        """
        Ждёт слот (семафор), исполняет задачу с SLA timeout, уведомляет пользователя.

        Приоритет реализован: owner запросы (PRIORITY_OWNER=0) создаются с task_priority=0,
        что позволяет asyncio планировщику запустить их быстрее при конкуренции за семафор.
        """
        start_ts = datetime.now()

        async with self._get_sem():
            bt.status = "RUNNING"
            self._pending_count = max(0, self._pending_count - 1)
            self._running_count += 1
            logger.info(
                "Task started: id=%s name=%s priority=%d running=%d",
                bt.id, bt.name, bt.priority, self._running_count,
            )

            try:
                bt.result = await asyncio.wait_for(coro, timeout=self.sla_timeout)
                bt.status = "COMPLETED"
                self._total_completed += 1

                # Уведомление об успехе
                if self.app and bt.result:
                    try:
                        await self.app.send_message(
                            bt.chat_id,
                            f"✅ **Задача завершена!**\nID: `{bt.id}`\n"
                            f"Результат: {str(bt.result)[:500]}",
                        )
                    except Exception:
                        pass  # Telegram уведомление не критично

            except (asyncio.TimeoutError, TimeoutError):
                # Python 3.11+: asyncio.wait_for поднимает builtins.TimeoutError
                # Оба типа ловим для совместимости
                bt.status = "FAILED"
                bt.error = f"SLA exceeded: {self.sla_timeout}s"
                self._total_failed += 1
                self._total_sla_aborts += 1
                logger.error(
                    "Task SLA abort: id=%s sla_timeout=%s",
                    bt.id, self.sla_timeout,
                )
                if self.app:
                    try:
                        await self.app.send_message(
                            bt.chat_id,
                            f"⚠️ **Задача прервана по SLA!**\n"
                            f"Процесс `🤔 Думаю...` занял более {int(self.sla_timeout)} секунд. "
                            f"Обработка остановлена для сохранения стабильности системы.",
                        )
                    except Exception:
                        pass

            except asyncio.CancelledError:
                # Явная отмена задачи (например, при shutdown)
                bt.status = "FAILED"
                bt.error = "Task cancelled"
                self._total_failed += 1
                raise  # Пробрасываем отмену дальше

            except Exception as exc:
                bt.status = "FAILED"
                bt.error = str(exc)
                self._total_failed += 1
                logger.error("Task failed: id=%s error=%s", bt.id, str(exc)[:200])
                if self.app:
                    try:
                        await self.app.send_message(
                            bt.chat_id,
                            f"❌ **Задача провалена!**\nID: `{bt.id}`\nОшибка: {exc}",
                        )
                    except Exception:
                        pass

            finally:
                bt.end_time = datetime.now()
                duration = (bt.end_time - start_ts).total_seconds()
                self._total_execution_time += duration
                self._running_count = max(0, self._running_count - 1)
                logger.debug(
                    "Task finished: id=%s status=%s duration_sec=%s",
                    bt.id, bt.status, round(duration, 2),
                )

    def _get_sem(self) -> asyncio.Semaphore:
        """
        Ленивая инициализация семафора — создаётся в текущем event loop.
        Это критично для pytest-asyncio, который создаёт новый loop для каждого теста.
        R25-core: после shutdown() семафор сброшен в None — при следующем enqueue
        создаётся заново в новом loop'е.
        """
        if self._running_sem is None:
            self._running_sem = asyncio.Semaphore(self.max_running)
        return self._running_sem
