"""Wave 101: тесты ShutdownCoordinator.

Покрывает: register/drain LIFO порядок, timeout per drain, slow drain
logging, isolation исключений (одно падает — следующие выполняются),
идемпотентность повторного drain_all, register_default_drains best-effort.
"""

from __future__ import annotations

import asyncio

import pytest

from src.bootstrap.shutdown_coordinator import (
    DEFAULT_DRAIN_TIMEOUT_SEC,
    SLOW_DRAIN_THRESHOLD_SEC,
    ShutdownCoordinator,
    register_default_drains,
    shutdown_coordinator,
)


@pytest.fixture
def coord() -> ShutdownCoordinator:
    """Свежий координатор на каждый тест — singleton не трогаем."""
    return ShutdownCoordinator()


@pytest.mark.asyncio
async def test_drain_runs_in_lifo_order(coord: ShutdownCoordinator) -> None:
    """Регистрация A, B, C → drain в порядке C, B, A."""
    order: list[str] = []

    async def make_drain(name: str) -> None:
        order.append(name)

    coord.register("A", lambda: make_drain("A"))
    coord.register("B", lambda: make_drain("B"))
    coord.register("C", lambda: make_drain("C"))

    outcomes = await coord.drain_all()

    assert order == ["C", "B", "A"]
    assert [o.name for o in outcomes] == ["C", "B", "A"]
    assert all(o.status == "ok" for o in outcomes)


@pytest.mark.asyncio
async def test_timeout_per_drain_logged(coord: ShutdownCoordinator) -> None:
    """Drain превышающий timeout → status=timeout, следующие drain'ы выполняются."""
    completed: list[str] = []

    async def slow_drain() -> None:
        await asyncio.sleep(2.0)  # точно превысит timeout=0.05
        completed.append("slow")

    async def fast_drain() -> None:
        completed.append("fast")

    # LIFO: fast зарегистрирован вторым → выполнится первым; slow второй.
    coord.register("slow", slow_drain, timeout_sec=0.05)
    coord.register("fast", fast_drain, timeout_sec=1.0)

    outcomes = await coord.drain_all()

    outcomes_by_name = {o.name: o for o in outcomes}
    assert outcomes_by_name["slow"].status == "timeout"
    assert outcomes_by_name["fast"].status == "ok"
    assert "fast" in completed
    assert "slow" not in completed  # был cancelled by timeout


@pytest.mark.asyncio
async def test_exception_in_drain_does_not_break_chain(
    coord: ShutdownCoordinator,
) -> None:
    """Drain бросает Exception → status=error, следующие drain'ы выполняются."""
    completed: list[str] = []

    async def boom() -> None:
        raise RuntimeError("simulated_drain_failure")

    async def ok_drain() -> None:
        completed.append("ok_drain")

    coord.register("boom", boom)
    coord.register("ok_drain", ok_drain)

    outcomes = await coord.drain_all()

    outcomes_by_name = {o.name: o for o in outcomes}
    assert outcomes_by_name["boom"].status == "error"
    assert "simulated_drain_failure" in (outcomes_by_name["boom"].error or "")
    assert outcomes_by_name["ok_drain"].status == "ok"
    assert "ok_drain" in completed


@pytest.mark.asyncio
async def test_slow_drain_logged(
    coord: ShutdownCoordinator,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Drain дольше SLOW_DRAIN_THRESHOLD_SEC → warning shutdown_drain_slow.

    structlog renders в stdout/stderr — используем capfd вместо caplog.
    """

    async def slow_but_ok() -> None:
        # Спим чуть дольше порога, но в пределах timeout.
        await asyncio.sleep(SLOW_DRAIN_THRESHOLD_SEC + 0.05)

    coord.register("slow_but_ok", slow_but_ok, timeout_sec=10.0)

    outcomes = await coord.drain_all()
    out, err = capfd.readouterr()

    assert outcomes[0].status == "ok"
    assert outcomes[0].duration_sec >= SLOW_DRAIN_THRESHOLD_SEC
    assert "shutdown_drain_slow" in (out + err)


@pytest.mark.asyncio
async def test_drain_all_idempotent(coord: ShutdownCoordinator) -> None:
    """Повторный вызов drain_all не запускает компоненты дважды."""
    calls: list[str] = []

    async def drain() -> None:
        calls.append("call")

    coord.register("once", drain)
    first = await coord.drain_all()
    second = await coord.drain_all()

    assert len(first) == 1
    assert len(second) == 0  # registry зачищен
    assert calls == ["call"]


@pytest.mark.asyncio
async def test_register_validation(coord: ShutdownCoordinator) -> None:
    """Невалидные аргументы register → ValueError/TypeError."""

    async def ok() -> None:
        return None

    with pytest.raises(ValueError):
        coord.register("", ok)
    with pytest.raises(TypeError):
        coord.register("bad", "not_callable")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        coord.register("zero", ok, timeout_sec=0)
    with pytest.raises(ValueError):
        coord.register("negative", ok, timeout_sec=-1)


@pytest.mark.asyncio
async def test_default_timeout_applied(coord: ShutdownCoordinator) -> None:
    """register без явного timeout → DEFAULT_DRAIN_TIMEOUT_SEC."""

    async def drain() -> None:
        return None

    coord.register("default", drain)
    assert coord._registry[0].timeout_sec == DEFAULT_DRAIN_TIMEOUT_SEC


def test_register_default_drains_is_safe() -> None:
    """register_default_drains не падает даже если модули отсутствуют.

    Использует module-level singleton — после теста чистим.
    """
    before = list(shutdown_coordinator.registered_names())
    try:
        register_default_drains()
    finally:
        # Откатываем регистрации этого теста, не трогая чужие.
        for name in (
            "swarm_activity_log",
            "translation_cache",
            "krab_ear_health_probe",
            "launchd_health_monitor",
        ):
            shutdown_coordinator.unregister(name)
        # Восстанавливаем исходный набор имён (если что-то было).
        assert set(shutdown_coordinator.registered_names()) == set(before)
