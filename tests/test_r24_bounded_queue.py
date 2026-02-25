# -*- coding: utf-8 -*-
"""
Тесты R24: Bounded Request Queue с backpressure и приоритетами.

Проверяют:
- enqueue возвращает None при переполненной очереди (backpressure)
- Приоритет owner/private задач над обычными
- Ограничение параллельных исполнений (max_running)
- Метрики включают rejected_count
- SLA timeout прерывает долгие задачи

Запуск:
    python -m pytest tests/test_r24_bounded_queue.py -v
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.core.bounded_queue import (
    BoundedRequestQueue,
    PRIORITY_OWNER,
    PRIORITY_NORMAL,
    QueueTask,
)


def make_queue(max_size: int = 5, max_running: int = 2, sla: float = 10) -> BoundedRequestQueue:
    """Хелпер: создаёт тестовую очередь с mock-клиентом."""
    mock_app = AsyncMock()
    mock_app.send_message = AsyncMock()
    config = {
        "QUEUE_MAX_SIZE": max_size,
        "QUEUE_MAX_RUNNING": max_running,
        "QUEUE_SLA_TIMEOUT": sla,
    }
    return BoundedRequestQueue(mock_app, config)


async def instant_coro(result="ok"):
    """Инстантная корутина для тестов."""
    return result


async def slow_coro(delay: float = 999):
    """Долгая корутина для тестов SLA."""
    await asyncio.sleep(delay)
    return "done"


class TestBoundedQueueBackpressure:
    """Тесты backpressure при переполнении очереди."""

    @pytest.mark.asyncio
    async def test_enqueue_returns_task_id_when_space_available(self):
        """Успешная постановка задачи возвращает task_id."""
        queue = make_queue(max_size=5)
        task_id = await queue.enqueue("test", 123, instant_coro("hi"))
        assert task_id is not None

    @pytest.mark.asyncio
    async def test_enqueue_returns_none_when_queue_full(self):
        """При заполненной очереди enqueue() возвращает None.

        R25-core: вместо slow_coro(999) используем Event-controlled корутины.
        Это позволяет чисто завершить все задачи через event.set() без утечек.
        """
        stop_event = asyncio.Event()

        async def controlled_coro():
            """Coroutine, которая ждёт сигнала через Event."""
            await stop_event.wait()

        # max_running=2 — обе задачи попадают в RUNNING state (не PENDING).
        # Важно: controlled_coro должна быть awaited внутри wait_for,
        # иначе shutdown().cancel() оставит корутину в neverawaited-состоянии → RuntimeWarning.
        queue = make_queue(max_size=2, max_running=2)
        await asyncio.sleep(0)  # Пропускаем планировщик до старта задач
        await queue.enqueue("t1", 1, controlled_coro())
        await queue.enqueue("t2", 2, controlled_coro())
        await asyncio.sleep(0)  # Позволяем event loop запустить _run_wrapper для t1/t2
        # Третья должна получить backpressure (max_size=2, pending+running=2)
        result = await queue.enqueue("t3", 3, instant_coro())
        assert result is None
        # Чисто завершаем все controlled coroutine’ы
        stop_event.set()
        await queue.shutdown(timeout=1.0)

    @pytest.mark.asyncio
    async def test_rejected_count_increments_on_backpressure(self):
        """rejected_count в метриках растёт при каждом backpressure.

        R25-core: используем Event-controlled корутину вместо slow_coro(999),
        чтобы избежать shutdown race condition и "never awaited" warning.
        """
        stop_event = asyncio.Event()

        async def controlled_coro():
            """Coroutine, которая ждёт сигнала через Event."""
            await stop_event.wait()

        # max_running=1 = max_size=1 — controlled_coro запускается сразу в RUNNING.
        # Следующие instant_coro запросы отклоняются через backpressure и enqueue закрывает их.
        queue = make_queue(max_size=1, max_running=1)
        await queue.enqueue("t1", 1, controlled_coro())
        await asyncio.sleep(0)  # Запускаем event loop чтобы _run_wrapper захватил семафор
        # Отклоняем 2 следующих — enqueue возвращает None (backpressure) и закрывает instant_coro
        r2 = await queue.enqueue("t2", 2, instant_coro())
        r3 = await queue.enqueue("t3", 3, instant_coro())
        assert r2 is None
        assert r3 is None

        metrics = queue.get_metrics()
        assert metrics["rejected_count"] >= 1
        # Чисто завершаем все controlled coroutine’ы
        stop_event.set()
        await queue.shutdown(timeout=1.0)

    @pytest.mark.asyncio
    async def test_metrics_include_max_queue_size(self):
        """Метрики содержат max_queue_size."""
        queue = make_queue(max_size=7)
        metrics = queue.get_metrics()
        assert metrics["max_queue_size"] == 7


class TestBoundedQueuePriority:
    """Тесты приоритизации задач."""

    @pytest.mark.asyncio
    async def test_owner_task_has_priority_zero(self):
        """Задача is_owner=True получает PRIORITY_OWNER=0."""
        queue = make_queue()
        task_id = await queue.enqueue("cmd", 1, instant_coro(), is_owner=True)
        assert task_id is not None
        task = queue.get_status(task_id)
        assert task is not None
        assert task.priority == PRIORITY_OWNER

    @pytest.mark.asyncio
    async def test_normal_task_has_priority_one(self):
        """Задача is_owner=False получает PRIORITY_NORMAL=1."""
        queue = make_queue()
        task_id = await queue.enqueue("msg", 999, instant_coro(), is_owner=False)
        task = queue.get_status(task_id)
        assert task.priority == PRIORITY_NORMAL

    @pytest.mark.asyncio
    async def test_explicit_priority_overrides_is_owner(self):
        """Явный priority перекрывает is_owner."""
        queue = make_queue()
        task_id = await queue.enqueue("msg", 1, instant_coro(), is_owner=False, priority=PRIORITY_OWNER)
        task = queue.get_status(task_id)
        assert task.priority == PRIORITY_OWNER


class TestBoundedQueueExecution:
    """Тесты выполнения задач."""

    @pytest.mark.asyncio
    async def test_completed_task_status(self):
        """Успешно выполненная задача переходит в COMPLETED."""
        queue = make_queue(sla=10)
        task_id = await queue.enqueue("fast", 1, instant_coro("result"))
        # Ждём завершения
        await asyncio.sleep(0.1)
        task = queue.get_status(task_id)
        assert task is not None
        assert task.status == "COMPLETED"
        assert task.result == "result"

    @pytest.mark.asyncio
    async def test_sla_timeout_aborts_slow_task(self):
        """Задача, превышающая SLA, получает status=FAILED."""
        queue = make_queue(sla=0.05)  # 50мс SLA
        task_id = await queue.enqueue("медленная", 1, slow_coro(999))
        await asyncio.sleep(0.5)    # Ждём SLA + завершение finally блока
        await asyncio.sleep(0)      # Дополнительный yield для планировщика
        task = queue.get_status(task_id)
        assert task is not None
        assert task.status == "FAILED"
        assert "SLA" in (task.error or "")
        # SLA-прwлай уже завершил задачу, shutdown пустой (no pending)
        await queue.shutdown(timeout=0.5)

    @pytest.mark.asyncio
    async def test_sla_abort_increments_counter(self):
        """SLA abort инкрементирует sla_aborts в метриках."""
        queue = make_queue(sla=0.05)
        await queue.enqueue("медленная", 1, slow_coro(999))
        await asyncio.sleep(0.5)
        await asyncio.sleep(0)      # yield для планировщика
        metrics = queue.get_metrics()
        assert metrics["sla_aborts"] >= 1
        # SLA-провал уже завершил, shutdown cleanup
        await queue.shutdown(timeout=0.5)


class TestBoundedQueueMetrics:
    """Тесты метрик очереди."""

    def test_initial_metrics_all_zero(self):
        """Начальные метрики — нули."""
        queue = make_queue()
        m = queue.get_metrics()
        assert m["active_tasks"] == 0
        assert m["waiting_tasks"] == 0
        assert m["completed_count"] == 0
        assert m["failed_count"] == 0
        assert m["sla_aborts"] == 0
        assert m["rejected_count"] == 0

    def test_metrics_has_all_required_fields(self):
        """get_metrics() содержит все ожидаемые поля."""
        queue = make_queue()
        m = queue.get_metrics()
        required = [
            "active_tasks", "waiting_tasks", "completed_count",
            "failed_count", "sla_aborts", "rejected_count",
            "avg_task_seconds", "total_tasks_ever",
            "max_queue_size", "max_running",
        ]
        for field in required:
            assert field in m, f"Поле '{field}' отсутствует в get_metrics()"


class TestBoundedQueueShutdown:
    """
    Тесты R25-core: graceful shutdown и отмена задач.

    Проверяют:
    - shutdown() корректно отменяет незавершённые задачи
    - После shutdown() enqueue() не принимает новые задачи
    - Нет RuntimeWarning при завершении очереди с незавершёнными задачами
    - Активные задачи в _active_tasks удаляются после завершения
    """

    @pytest.mark.asyncio
    async def test_shutdown_cancels_running_tasks(self):
        """shutdown() отменяет незавершённые задачи без утечек."""
        queue = make_queue(sla=30.0)  # длинный SLA — задачи не завершатся сами

        # Ставим долгую задачу в очередь
        task_id = await queue.enqueue("long", 1, slow_coro(999))
        assert task_id is not None

        # Небольшая пауза — задача должна начать выполнение
        await asyncio.sleep(0.05)

        # Graceful shutdown — задача должна быть отменена
        await queue.shutdown(timeout=1.0)

        # Семафор сброшен после shutdown (для поддержки новых loop'ов)
        assert queue._running_sem is None
        # _active_tasks пустой после shutdown
        assert len(queue._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_blocks_new_enqueue(self):
        """После shutdown() новые задачи не принимаются (enqueue → None)."""
        queue = make_queue()
        await queue.shutdown()

        # Создаём корутину явно — enqueue должен её закрыть сам
        coro = slow_coro(999)
        result = await queue.enqueue("test", 1, coro)
        assert result is None  # backpressure через shutdown-флаг

    @pytest.mark.asyncio
    async def test_active_tasks_set_cleaned_after_complete(self):
        """Завершённые задачи автоматически удаляются из _active_tasks."""
        queue = make_queue(sla=10.0)
        await queue.enqueue("fast", 1, instant_coro("x"))

        # Ждём завершения фоновой задачи
        await asyncio.sleep(0.2)

        # После завершения callback удалил задачу из _active_tasks
        assert len(queue._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_shutdown_empty_queue_no_error(self):
        """shutdown() пустой очереди не вызывает ошибок."""
        queue = make_queue()
        # Не должно бросить никаких исключений
        await queue.shutdown(timeout=1.0)
        assert queue._shutdown is True

