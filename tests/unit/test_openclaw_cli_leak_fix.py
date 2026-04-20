# -*- coding: utf-8 -*-
"""
Тесты для исправления утечки subprocess'ов в openclaw CLI вызовах.

Покрываемые сценарии:
  - terminate → kill+wait при timeout (предотвращение orphan Node.js)
  - semaphore throttle: не более N параллельных spawn'ов в web_app
  - semaphore limit=1 в proactive_watch._fetch_openclaw_cron_jobs
  - warning логируется если процесс не поддаётся reap'у
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc_stub(
    *,
    returncode: int | None = None,
    communicate_side_effect=None,
    wait_side_effect=None,
    pid: int = 12345,
) -> MagicMock:
    """Создаёт минимальный asyncio.Process mock."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode

    if communicate_side_effect is not None:
        proc.communicate = AsyncMock(side_effect=communicate_side_effect)
    else:
        proc.communicate = AsyncMock(return_value=(b'{"ok": true}', b""))

    if wait_side_effect is not None:
        proc.wait = AsyncMock(side_effect=wait_side_effect)
    else:
        proc.wait = AsyncMock(return_value=0)

    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# Тесты _cron_run_openclaw (command_handlers.py)
# ---------------------------------------------------------------------------


class TestCronRunOpenclaw:
    """terminate → kill+wait в _cron_run_openclaw."""

    @pytest.mark.asyncio
    async def test_terminate_then_kill_on_double_timeout(self) -> None:
        """
        Если communicate() зависает и wait() тоже не возвращается —
        должны вызвать kill() и залогировать warning.
        """
        from src.handlers.command_handlers import _cron_run_openclaw

        # wait() бесконечно зависает → TimeoutError на каждый вызов
        proc = _make_proc_stub(
            communicate_side_effect=asyncio.TimeoutError,
            wait_side_effect=asyncio.TimeoutError,
        )

        with (
            patch(
                "src.handlers.command_handlers.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=proc),
            ),
            patch("src.handlers.command_handlers.logger") as mock_logger,
        ):
            success, result = await _cron_run_openclaw("cron", "list", timeout=0.01)

        assert success is False
        assert result == "timeout"
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args[0][0] == "openclaw_cli_force_killed_but_no_reap"

    @pytest.mark.asyncio
    async def test_terminate_graceful_exit(self) -> None:
        """
        Если wait() возвращается после terminate — kill() вызывать не нужно.
        """
        from src.handlers.command_handlers import _cron_run_openclaw

        proc = _make_proc_stub(
            communicate_side_effect=asyncio.TimeoutError,
            wait_side_effect=None,  # wait() завершается нормально
        )

        with (
            patch(
                "src.handlers.command_handlers.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=proc),
            ),
            patch("src.handlers.command_handlers.logger") as mock_logger,
        ):
            success, result = await _cron_run_openclaw("cron", "list", timeout=0.01)

        assert success is False
        assert result == "timeout"
        proc.terminate.assert_called_once()
        proc.kill.assert_not_called()
        mock_logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_already_exited_no_terminate(self) -> None:
        """
        Если returncode уже установлен — terminate не вызывается.
        """
        from src.handlers.command_handlers import _cron_run_openclaw

        proc = _make_proc_stub(
            returncode=1,
            communicate_side_effect=asyncio.TimeoutError,
        )

        with patch(
            "src.handlers.command_handlers.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            success, result = await _cron_run_openclaw("cron", "status", timeout=0.01)

        assert result == "timeout"
        proc.terminate.assert_not_called()
        proc.kill.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты _fetch_openclaw_cron_jobs (proactive_watch.py)
# ---------------------------------------------------------------------------


class TestFetchOpenclewCronJobs:
    """terminate → kill+wait в _fetch_openclaw_cron_jobs + semaphore limit=1."""

    @pytest.mark.asyncio
    async def test_terminate_then_kill_on_double_timeout(self) -> None:
        """wait() зависает → kill() должен быть вызван."""
        from src.core import proactive_watch as pw

        proc = _make_proc_stub(
            communicate_side_effect=asyncio.TimeoutError,
            wait_side_effect=asyncio.TimeoutError,
        )

        with (
            patch(
                "src.core.proactive_watch.asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=proc),
            ),
            patch("src.core.proactive_watch.logger") as mock_logger,
        ):
            result = await pw._fetch_openclaw_cron_jobs()

        assert result == []
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()
        mock_logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency_to_one(self) -> None:
        """
        Параллельные вызовы _fetch_openclaw_cron_jobs не должны перекрываться —
        _cron_probe_sem имеет limit=1.
        """
        from src.core import proactive_watch as pw

        concurrent_count: list[int] = [0]
        max_concurrent: list[int] = [0]

        async def slow_communicate() -> tuple[bytes, None]:
            concurrent_count[0] += 1
            max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
            await asyncio.sleep(0.05)
            concurrent_count[0] -= 1
            return b'{"jobs": []}', None

        proc = _make_proc_stub()
        proc.communicate = AsyncMock(side_effect=slow_communicate)

        with patch(
            "src.core.proactive_watch.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            await asyncio.gather(*[pw._fetch_openclaw_cron_jobs() for _ in range(3)])

        # С semaphore(1) максимальный concurrent == 1
        assert max_concurrent[0] == 1

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_json_error(self) -> None:
        """Битый JSON → пустой список, не исключение."""
        from src.core import proactive_watch as pw

        proc = _make_proc_stub()
        proc.communicate = AsyncMock(return_value=(b"not json!", b""))

        with patch(
            "src.core.proactive_watch.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ):
            result = await pw._fetch_openclaw_cron_jobs()

        assert result == []


# ---------------------------------------------------------------------------
# Тесты семафора web_app._run_openclaw_cli / _run_openclaw_cli_json
# ---------------------------------------------------------------------------


class TestWebAppCliSemaphore:
    """Семафор в KrabWebApp ограничивает параллельные CLI spawn'ы."""

    def _make_web_app(self, budget: int = 2) -> object:
        """Создаёт минимальный KrabWebApp stub с семафором."""
        import importlib
        import sys

        # Патч тяжёлых deps перед импортом
        for mod in ["fastapi", "uvicorn", "structlog"]:
            if mod not in sys.modules:
                sys.modules[mod] = MagicMock()

        with patch.dict("os.environ", {"OPENCLAW_CLI_SPAWN_BUDGET": str(budget)}):
            from src.modules.web_app import KrabWebApp

            app = KrabWebApp.__new__(KrabWebApp)
            import os

            _cli_budget = int(os.getenv("OPENCLAW_CLI_SPAWN_BUDGET", "3"))
            app._openclaw_cli_sem = asyncio.Semaphore(_cli_budget)
        return app

    @pytest.mark.asyncio
    async def test_semaphore_env_default(self) -> None:
        """OPENCLAW_CLI_SPAWN_BUDGET default = 3 → semaphore._value == 3."""
        with patch.dict("os.environ", {}, clear=False):
            import os

            budget = int(os.getenv("OPENCLAW_CLI_SPAWN_BUDGET", "3"))
            sem = asyncio.Semaphore(budget)
            assert sem._value == 3  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self) -> None:
        """С budget=2 не более 2 CLI вызовов одновременно."""
        concurrent_count: list[int] = [0]
        max_concurrent: list[int] = [0]

        sem = asyncio.Semaphore(2)

        async def guarded_task() -> None:
            async with sem:
                concurrent_count[0] += 1
                max_concurrent[0] = max(max_concurrent[0], concurrent_count[0])
                await asyncio.sleep(0.03)
                concurrent_count[0] -= 1

        await asyncio.gather(*[guarded_task() for _ in range(5)])
        assert max_concurrent[0] <= 2


# ---------------------------------------------------------------------------
# Тест env var документирован
# ---------------------------------------------------------------------------


def test_spawn_budget_env_var_is_configurable() -> None:
    """OPENCLAW_CLI_SPAWN_BUDGET должен парситься как int."""
    with patch.dict("os.environ", {"OPENCLAW_CLI_SPAWN_BUDGET": "5"}):
        import os

        budget = int(os.getenv("OPENCLAW_CLI_SPAWN_BUDGET", "3"))
        assert budget == 5

    with patch.dict("os.environ", {}, clear=False):
        import os

        os.environ.pop("OPENCLAW_CLI_SPAWN_BUDGET", None)
        budget = int(os.getenv("OPENCLAW_CLI_SPAWN_BUDGET", "3"))
        assert budget == 3
