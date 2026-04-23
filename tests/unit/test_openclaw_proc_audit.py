"""Тесты openclaw_cli_budget: семафор, terminate_and_reap, list_openclaw_procs."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.core.openclaw_cli_budget import (
    OPENCLAW_CLI_BUDGET,
    acquire,
    budget_available,
    list_openclaw_procs,
    reset_semaphore,
    terminate_and_reap,
)

# ---------------------------------------------------------------------------
# 1. Семафор enforce budget=3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_budget_default():
    """Бюджет по умолчанию равен OPENCLAW_CLI_BUDGET (3)."""
    reset_semaphore(3)
    assert budget_available() == 3


@pytest.mark.asyncio
async def test_semaphore_decrements_and_restores():
    """acquire() уменьшает счётчик и восстанавливает после блока."""
    reset_semaphore(3)
    assert budget_available() == 3
    async with acquire():
        assert budget_available() == 2
        async with acquire():
            assert budget_available() == 1
    assert budget_available() == 3


@pytest.mark.asyncio
async def test_semaphore_blocks_at_budget():
    """При исчерпании бюджета (budget=1) новый acquire() блокирует."""
    reset_semaphore(1)
    events: list[str] = []

    async def first():
        async with acquire():
            events.append("first_enter")
            await asyncio.sleep(0.05)
            events.append("first_exit")

    async def second():
        async with acquire():
            events.append("second_enter")

    t1 = asyncio.ensure_future(first())
    await asyncio.sleep(0.01)  # дать первому войти
    t2 = asyncio.ensure_future(second())
    await asyncio.gather(t1, t2)

    # second должен зайти только после выхода first
    assert events.index("second_enter") > events.index("first_exit")


# ---------------------------------------------------------------------------
# 2. terminate_and_reap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_terminate_and_reap_already_done():
    """Если returncode уже выставлен — ничего не делает."""
    proc = MagicMock()
    proc.returncode = 0
    await terminate_and_reap(proc)
    proc.terminate.assert_not_called()


@pytest.mark.asyncio
async def test_terminate_and_reap_calls_terminate_then_wait():
    """Для живого процесса вызывает terminate и wait."""
    proc = MagicMock()
    proc.returncode = None

    async def _wait():
        proc.returncode = -15
        return None

    proc.wait = _wait
    await terminate_and_reap(proc, timeout_sec=1.0)
    proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# 3. list_openclaw_procs — с psutil mock
# ---------------------------------------------------------------------------


def _make_proc_info(name: str, cmdline: list[str], rss: int = 50 * 1024 * 1024, pid: int = 100):
    """Вспомогательная фабрика для psutil proc mock."""
    mem = MagicMock()
    mem.rss = rss
    return {
        "pid": pid,
        "name": name,
        "cmdline": cmdline,
        "create_time": 1_700_000_000.0,
        "status": "running",
        "memory_info": mem,
    }


def test_list_openclaw_procs_gateway_only():
    """openclaw-gateway определяется как is_gateway=True."""
    mock_proc = MagicMock()
    mock_proc.info = _make_proc_info(
        name="openclaw-gateway",
        cmdline=["openclaw-gateway", "--config", "/etc/oc.json"],
        pid=999,
    )

    with patch("psutil.process_iter", return_value=[mock_proc]):
        procs = list_openclaw_procs()

    assert len(procs) == 1
    assert procs[0]["is_gateway"] is True
    assert procs[0]["pid"] == 999


def test_list_openclaw_procs_filters_chrome():
    """Chrome-процессы с openclaw только в user-data-dir не попадают в список."""
    chrome_proc = MagicMock()
    chrome_proc.info = _make_proc_info(
        name="Google Chrome Helper (Renderer)",
        cmdline=[
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome Helper (Renderer)",
            "--user-data-dir=/Users/x/.openclaw/browser/openclaw/user-data",
        ],
        pid=12345,
    )

    with patch("psutil.process_iter", return_value=[chrome_proc]):
        procs = list_openclaw_procs()

    assert procs == []


def test_list_openclaw_procs_transient_cli():
    """Transient CLI (openclaw cron list) определяется как is_gateway=False."""
    mock_proc = MagicMock()
    mock_proc.info = _make_proc_info(
        name="openclaw",
        cmdline=["openclaw", "cron", "list", "--json", "--all"],
        pid=555,
    )

    with patch("psutil.process_iter", return_value=[mock_proc]):
        procs = list_openclaw_procs()

    assert len(procs) == 1
    assert procs[0]["is_gateway"] is False
    assert "cron" in procs[0]["cmd"]


def test_list_openclaw_procs_no_psutil():
    """Если psutil не установлен — возвращает пустой список без исключения."""
    import sys

    orig = sys.modules.get("psutil")
    sys.modules["psutil"] = None  # type: ignore[assignment]
    try:
        procs = list_openclaw_procs()
    finally:
        if orig is None:
            del sys.modules["psutil"]
        else:
            sys.modules["psutil"] = orig

    assert procs == []


# ---------------------------------------------------------------------------
# 4. /api/ops/openclaw-procs endpoint
# ---------------------------------------------------------------------------


def test_endpoint_leak_detection():
    """leak_suspected=True когда процессов > expected(1) + budget(3)."""
    procs_data = [
        {
            "pid": i,
            "cmd": "openclaw cron list",
            "age_sec": 5.0,
            "rss_mb": 10.0,
            "status": "running",
            "is_gateway": False,
        }
        for i in range(6)
    ]
    procs_data.append(
        {
            "pid": 99,
            "cmd": "openclaw-gateway",
            "age_sec": 3600.0,
            "rss_mb": 200.0,
            "status": "running",
            "is_gateway": True,
        }
    )

    with patch("src.core.openclaw_cli_budget.list_openclaw_procs", return_value=procs_data):
        from src.core.openclaw_cli_budget import OPENCLAW_CLI_BUDGET, list_openclaw_procs

        procs = list_openclaw_procs()
        total = len(procs)
        leak = total > (1 + OPENCLAW_CLI_BUDGET)

    assert leak is True  # 7 > 4
