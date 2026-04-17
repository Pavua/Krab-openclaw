"""
Correlation ID (request_id) через structlog contextvars.

Session 10 #5 — связка логов одного запроса через весь pipeline.

Проверяем:
- bind/clear — логи подхватывают request_id автоматически
- parallel asyncio.create_task — контексты изолированы между task'ами
- clear_contextvars в finally — после выхода request_id исчезает
- asyncio.create_task наследует contextvars родителя (Python 3.7+)
"""

from __future__ import annotations

import asyncio

import pytest
import structlog
from structlog.testing import capture_logs

from src.core.logger import (
    bind_contextvars,
    clear_contextvars,
    merge_contextvars,
    run_in_contextvars_copy,
    unbind_contextvars,
)


@pytest.fixture(autouse=True)
def _configure_structlog_with_merge_contextvars():
    """
    Убеждаемся что merge_contextvars в цепочке — иначе capture_logs не увидит
    request_id из contextvars. capture_logs подменяет processors, поэтому
    перед каждым тестом реконфигурируем structlog канонически.
    """
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.EventRenamer("event"),
        ],
        context_class=dict,
        cache_logger_on_first_use=False,
    )
    clear_contextvars()  # предварительная очистка — тест-fixture isolation
    yield
    clear_contextvars()


def test_bind_and_log_has_request_id():
    """bind_contextvars → logger.info → event dict содержит request_id."""
    logger = structlog.get_logger("test_bind")
    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="abc123def456")
        logger.info("test_event")
        clear_contextvars()

    # capture_logs возвращает event dict без рендеринга
    matching = [e for e in cap_logs if e.get("request_id") == "abc123def456"]
    assert len(matching) == 1, f"Expected exactly one event with request_id, got: {cap_logs}"
    assert matching[0].get("event") == "test_event"


def test_clear_removes_request_id():
    """После clear_contextvars — logger.info НЕ должен содержать request_id."""
    logger = structlog.get_logger("test_clear")
    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="should_be_cleared")
        clear_contextvars()
        logger.info("after_clear_event")

    event = next((e for e in cap_logs if e.get("event") == "after_clear_event"), None)
    assert event is not None
    assert "request_id" not in event, f"request_id должен быть очищен, но есть: {event}"


def test_unbind_specific_var():
    """unbind_contextvars убирает только указанную переменную."""
    logger = structlog.get_logger("test_unbind")
    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="kept_req", chat_id="kept_chat", user_id="to_remove")
        unbind_contextvars("user_id")
        logger.info("partial_unbind")

    event = next((e for e in cap_logs if e.get("event") == "partial_unbind"), None)
    assert event is not None
    assert event.get("request_id") == "kept_req"
    assert event.get("chat_id") == "kept_chat"
    assert "user_id" not in event


def test_finally_clears_on_exception():
    """try/finally паттерн — clear_contextvars срабатывает даже при исключении."""
    logger = structlog.get_logger("test_finally")

    async def _process():
        bind_contextvars(request_id="finally_rid")
        try:
            raise RuntimeError("simulated failure")
        finally:
            clear_contextvars()

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        with pytest.raises(RuntimeError):
            asyncio.run(_process())
        # После finally — request_id очищен
        logger.info("after_finally_event")

    event = next((e for e in cap_logs if e.get("event") == "after_finally_event"), None)
    assert event is not None
    assert "request_id" not in event


@pytest.mark.asyncio
async def test_parallel_asyncio_tasks_isolated():
    """
    Два asyncio.create_task с разными request_id не смешивают контексты.

    Python 3.7+ автоматически копирует Context при create_task, поэтому
    bind_contextvars внутри одной task'и не влияет на другую.
    """
    logger = structlog.get_logger("test_parallel")

    async def _worker(rid: str, delay: float) -> str:
        bind_contextvars(request_id=rid)
        try:
            await asyncio.sleep(delay)
            logger.info("worker_event", worker_rid=rid)
            return rid
        finally:
            clear_contextvars()

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        # Стартуем обе task'и сначала — они должны работать конкурентно
        t1 = asyncio.create_task(_worker("rid_alpha_1234", 0.01))
        t2 = asyncio.create_task(_worker("rid_beta_5678", 0.005))
        await asyncio.gather(t1, t2)

    worker_events = [e for e in cap_logs if e.get("event") == "worker_event"]
    assert len(worker_events) == 2

    # Каждый event должен содержать request_id, соответствующий своему worker_rid
    for ev in worker_events:
        assert ev.get("request_id") == ev.get("worker_rid"), f"Контексты смешались: {ev}"


@pytest.mark.asyncio
async def test_create_task_inherits_parent_context():
    """
    asyncio.create_task наследует contextvars родителя (если сам не биндит
    ничего нового). Ключевое свойство для swarm runs.
    """
    logger = structlog.get_logger("test_inherit")

    async def _child_without_bind() -> None:
        # Не вызываем bind — должны унаследовать родительский request_id
        logger.info("child_event")

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="parent_rid_9999")
        try:
            child_task = asyncio.create_task(_child_without_bind())
            await child_task
        finally:
            clear_contextvars()

    child_event = next((e for e in cap_logs if e.get("event") == "child_event"), None)
    assert child_event is not None
    assert child_event.get("request_id") == "parent_rid_9999"


@pytest.mark.asyncio
async def test_child_bind_isolates_from_parent():
    """
    Дочерняя задача может забиндить свой request_id и не ломает родительский
    (структурно: create_task копирует Context, дочерние изменения не
    пробиваются обратно вверх).
    """
    logger = structlog.get_logger("test_child_isolation")

    async def _child_rebinds() -> None:
        bind_contextvars(request_id="child_rid_override")
        try:
            logger.info("child_after_rebind")
        finally:
            clear_contextvars()

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="parent_rid_keep")
        try:
            await asyncio.create_task(_child_rebinds())
            # После завершения child — родитель всё ещё видит свой rid
            logger.info("parent_after_child")
        finally:
            clear_contextvars()

    child_ev = next((e for e in cap_logs if e.get("event") == "child_after_rebind"), None)
    parent_ev = next((e for e in cap_logs if e.get("event") == "parent_after_child"), None)
    assert child_ev is not None and child_ev.get("request_id") == "child_rid_override"
    assert parent_ev is not None and parent_ev.get("request_id") == "parent_rid_keep"


@pytest.mark.asyncio
async def test_to_thread_inherits_context():
    """
    asyncio.to_thread пробрасывает contextvars (Python 3.9+). Важно для
    memory_indexer и других to_thread-call'ов.
    """
    logger = structlog.get_logger("test_to_thread")

    def _sync_worker() -> None:
        # Sync worker внутри thread pool — должен видеть contextvars родителя
        logger.info("thread_event")

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="thread_rid_7777")
        try:
            await asyncio.to_thread(_sync_worker)
        finally:
            clear_contextvars()

    thread_ev = next((e for e in cap_logs if e.get("event") == "thread_event"), None)
    assert thread_ev is not None
    assert thread_ev.get("request_id") == "thread_rid_7777"


@pytest.mark.asyncio
async def test_run_in_contextvars_copy_isolates():
    """
    run_in_contextvars_copy hel helper — запуск в скопированном контексте
    защищает вызывающего от finally-clear внутри подзадачи.
    """
    logger = structlog.get_logger("test_copy_ctx")

    async def _inner_clears() -> None:
        bind_contextvars(request_id="inner_rid")
        try:
            logger.info("inner_event")
        finally:
            clear_contextvars()

    with capture_logs(processors=[merge_contextvars]) as cap_logs:
        bind_contextvars(request_id="outer_rid_keep")
        try:
            # Запускаем через copy_context — clear внутри не затронет outer
            await run_in_contextvars_copy(_inner_clears)
            logger.info("outer_event_after")
        finally:
            clear_contextvars()

    outer_after = next((e for e in cap_logs if e.get("event") == "outer_event_after"), None)
    assert outer_after is not None
    # outer должен сохранить свой request_id несмотря на inner clear
    assert outer_after.get("request_id") == "outer_rid_keep"
    # И inner должен видеть свой собственный request_id
    inner_ev = next((e for e in cap_logs if e.get("event") == "inner_event"), None)
    assert inner_ev is not None and inner_ev.get("request_id") == "inner_rid"
