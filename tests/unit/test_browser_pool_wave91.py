# -*- coding: utf-8 -*-
"""Wave 91: BrowserSessionPool tests.

Coverage:
  - health check pass / fail
  - recycle on age
  - recycle on error_count
  - pool size limit
  - health_audit drops stale
  - context manager increments error_count on exception
  - close_all освобождает сессии
"""

from __future__ import annotations

import asyncio

import pytest

from src.integrations import browser_pool as bp
from src.integrations.browser_pool import BrowserSessionPool


class FakeSession:
    """Mock BrowserBridge — execute_js возвращает 2, есть close()."""

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.closed = False
        self.execute_js_calls = 0

    async def execute_js(self, code: str):
        self.execute_js_calls += 1
        if not self.healthy:
            raise RuntimeError("simulated stale CDP")
        if code == "1+1":
            return 2
        return None

    async def close(self) -> None:
        self.closed = True


def _make_factory(sessions: list[FakeSession]):
    """Создаёт factory, выдающий поочерёдно сессии из списка."""
    iterator = iter(sessions)

    async def _factory():
        try:
            return next(iterator)
        except StopIteration:
            # Если тест запросил больше сессий — создаём свежую healthy.
            fresh = FakeSession()
            sessions.append(fresh)
            return fresh

    return _factory


@pytest.mark.asyncio
async def test_health_check_pass_returns_session():
    """Здоровая сессия проходит health check и выдаётся через borrow."""
    sess = FakeSession(healthy=True)
    pool = BrowserSessionPool(factory=_make_factory([sess]), max_size=2)
    # Первый borrow — свежая сессия, health check не нужен.
    async with pool.borrow() as borrowed:
        assert borrowed is sess
    assert sess.execute_js_calls == 0
    # Второй borrow — health check над переиспользуемой сессией.
    async with pool.borrow() as borrowed:
        assert borrowed is sess
    assert sess.execute_js_calls == 1
    assert not sess.closed
    assert pool.active_count == 1


@pytest.mark.asyncio
async def test_health_check_fail_triggers_recycle():
    """Если health_check падает, сессия recycle-ится и factory вызывается повторно."""
    bad = FakeSession(healthy=False)
    good = FakeSession(healthy=True)
    pool = BrowserSessionPool(factory=_make_factory([bad, good]), max_size=2)

    # Подсаживаем bad в пул вручную (имитируем "была живая, потом протухла").
    bad_pooled = await pool._create_session()
    bad_pooled.in_use = False

    async with pool.borrow() as borrowed:
        # bad должен быть закрыт по health_fail, выдан good.
        assert borrowed is good

    assert bad.closed is True
    assert good.closed is False


@pytest.mark.asyncio
async def test_recycle_on_age():
    """Сессия старше max_age recycles при следующем borrow."""
    clock = [1_000_000.0]
    sess_old = FakeSession()
    sess_new = FakeSession()
    pool = BrowserSessionPool(
        factory=_make_factory([sess_old, sess_new]),
        max_age_sec=10,
        max_size=2,
        now_fn=lambda: clock[0],
    )

    pooled = await pool._create_session()
    pooled.in_use = False
    # Сдвигаем "время" — сессия теперь старше max_age_sec.
    clock[0] += 100.0

    async with pool.borrow() as borrowed:
        assert borrowed is sess_new
    assert sess_old.closed is True


@pytest.mark.asyncio
async def test_recycle_on_error_count():
    """error_count > max_errors → recycle."""
    sess1 = FakeSession()
    sess2 = FakeSession()
    pool = BrowserSessionPool(
        factory=_make_factory([sess1, sess2]),
        max_errors=2,
        max_size=2,
    )

    # Borrow + 3 ошибки подряд.
    for _ in range(3):
        with pytest.raises(RuntimeError, match="boom"):
            async with pool.borrow():
                raise RuntimeError("boom")

    # После 3-й ошибки sess1.error_count > max_errors (2) → закрыта.
    assert sess1.closed is True
    # Следующий borrow выдаст sess2.
    async with pool.borrow() as borrowed:
        assert borrowed is sess2


@pytest.mark.asyncio
async def test_pool_size_limit():
    """При max_size=1 второй параллельный acquire падает с pool_exhausted."""
    sess = FakeSession()
    pool = BrowserSessionPool(factory=_make_factory([sess]), max_size=1)

    pooled = await pool.acquire()
    assert pooled.in_use is True

    with pytest.raises(RuntimeError, match="browser_pool_exhausted"):
        await pool.acquire()

    await pool.release(pooled)


@pytest.mark.asyncio
async def test_health_audit_drops_stale_age():
    """health_audit() закрывает свободные просроченные сессии."""
    clock = [1_000_000.0]
    sess = FakeSession()
    pool = BrowserSessionPool(
        factory=_make_factory([sess]),
        max_age_sec=10,
        max_size=2,
        now_fn=lambda: clock[0],
    )

    pooled = await pool._create_session()
    pooled.in_use = False
    clock[0] += 100.0

    dropped = await pool.health_audit()
    assert dropped["age"] == 1
    assert sess.closed is True
    assert pool.active_count == 0


@pytest.mark.asyncio
async def test_close_all_releases_sessions():
    """close_all закрывает все сессии и обнуляет active_count."""
    s1 = FakeSession()
    s2 = FakeSession()
    pool = BrowserSessionPool(factory=_make_factory([s1, s2]), max_size=3)
    await pool._create_session()
    await pool._create_session()
    assert pool.active_count == 2

    await pool.close_all()
    assert s1.closed and s2.closed
    assert pool.active_count == 0


@pytest.mark.asyncio
async def test_env_helpers_defaults(monkeypatch):
    """Env helpers возвращают defaults при отсутствии/невалидных значениях."""
    monkeypatch.delenv("KRAB_BROWSER_POOL_ENABLED", raising=False)
    monkeypatch.delenv("KRAB_BROWSER_POOL_MAX_AGE_SEC", raising=False)
    assert bp.pool_enabled() is False
    assert bp.pool_max_age_sec() == 1800

    monkeypatch.setenv("KRAB_BROWSER_POOL_ENABLED", "1")
    monkeypatch.setenv("KRAB_BROWSER_POOL_MAX_AGE_SEC", "60")
    assert bp.pool_enabled() is True
    assert bp.pool_max_age_sec() == 60

    monkeypatch.setenv("KRAB_BROWSER_POOL_MAX_AGE_SEC", "garbage")
    assert bp.pool_max_age_sec() == 1800


@pytest.mark.asyncio
async def test_audit_loop_cancellable():
    """run_pool_audit_loop корректно отменяется по CancelledError."""
    sess = FakeSession()
    pool = BrowserSessionPool(factory=_make_factory([sess]), max_size=1)
    task = asyncio.create_task(bp.run_pool_audit_loop(pool, interval_sec=5))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
