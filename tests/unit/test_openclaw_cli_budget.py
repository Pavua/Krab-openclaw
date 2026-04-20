"""Тесты для src/core/openclaw_cli_budget.py (Wave 4)."""
from __future__ import annotations

import asyncio
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_module():
    """Возвращает свежий модуль openclaw_cli_budget без кешированного синглтона."""
    mod_name = "src.core.openclaw_cli_budget"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    import importlib

    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# get_global_semaphore
# ---------------------------------------------------------------------------


class TestGetGlobalSemaphore(unittest.TestCase):
    def setUp(self):
        self.mod = _fresh_module()

    def test_returns_semaphore(self):
        sem = self.mod.get_global_semaphore()
        self.assertIsInstance(sem, asyncio.Semaphore)

    def test_singleton_same_instance(self):
        sem1 = self.mod.get_global_semaphore()
        sem2 = self.mod.get_global_semaphore()
        self.assertIs(sem1, sem2)

    def test_budget_default_three(self):
        # Дефолтный бюджет — 3 (OPENCLAW_CLI_SPAWN_BUDGET не задан в тестах)
        with patch.dict("os.environ", {}, clear=False):
            mod = _fresh_module()
            self.assertEqual(mod._BUDGET, 3)

    def test_budget_from_env(self):
        with patch.dict("os.environ", {"OPENCLAW_CLI_SPAWN_BUDGET": "7"}):
            mod = _fresh_module()
            self.assertEqual(mod._BUDGET, 7)


# ---------------------------------------------------------------------------
# get_sync_semaphore
# ---------------------------------------------------------------------------


class TestGetSyncSemaphore(unittest.TestCase):
    def setUp(self):
        self.mod = _fresh_module()

    def test_returns_threading_semaphore(self):
        import threading

        sem = self.mod.get_sync_semaphore()
        self.assertIsInstance(sem, threading.Semaphore)

    def test_singleton_same_instance(self):
        sem1 = self.mod.get_sync_semaphore()
        sem2 = self.mod.get_sync_semaphore()
        self.assertIs(sem1, sem2)


# ---------------------------------------------------------------------------
# terminate_and_reap
# ---------------------------------------------------------------------------


class TestTerminateAndReap(unittest.IsolatedAsyncioTestCase):
    async def test_clean_exit_no_kill(self):
        """Процесс завершился сам — SIGKILL не должен отправляться."""
        mod = _fresh_module()

        proc = MagicMock()
        proc.terminate = MagicMock()
        # wait() сразу возвращает без таймаута
        proc.wait = AsyncMock(return_value=0)

        await mod.terminate_and_reap(proc)

        proc.terminate.assert_called_once()
        proc.kill.assert_not_called() if hasattr(proc, "kill") else None

    async def test_sigterm_timeout_sends_sigkill(self):
        """Если SIGTERM не помогает — должен отправиться SIGKILL."""
        mod = _fresh_module()

        kill_called = []
        kill_wait_called = []

        proc = MagicMock()
        proc.terminate = MagicMock()
        proc.kill = MagicMock(side_effect=lambda: kill_called.append(True))

        call_count = [0]

        async def mock_wait():
            call_count[0] += 1
            if call_count[0] == 1:
                # Первый wait() — после SIGTERM — зависает
                raise asyncio.TimeoutError()
            kill_wait_called.append(True)
            return 0

        proc.wait = mock_wait

        await mod.terminate_and_reap(proc, term_grace=0.01, kill_grace=0.01)

        self.assertEqual(len(kill_called), 1, "kill() должен быть вызван один раз")
        self.assertEqual(len(kill_wait_called), 1, "wait() после kill должен быть вызван")

    async def test_process_already_gone_terminate(self):
        """ProcessLookupError при terminate — тихо игнорируется."""
        mod = _fresh_module()

        proc = MagicMock()
        proc.terminate = MagicMock(side_effect=ProcessLookupError)

        # Должно завершиться без исключений
        await mod.terminate_and_reap(proc)

    async def test_process_already_gone_kill(self):
        """ProcessLookupError при kill — тихо игнорируется."""
        mod = _fresh_module()

        proc = MagicMock()
        proc.terminate = MagicMock()

        call_count = [0]

        async def mock_wait():
            call_count[0] += 1
            raise asyncio.TimeoutError()

        proc.wait = mock_wait
        proc.kill = MagicMock(side_effect=ProcessLookupError)

        # Должно завершиться без исключений
        await mod.terminate_and_reap(proc, term_grace=0.01, kill_grace=0.01)


# ---------------------------------------------------------------------------
# Semaphore concurrency limit
# ---------------------------------------------------------------------------


class TestSemaphoreLimits(unittest.IsolatedAsyncioTestCase):
    async def test_semaphore_limits_concurrency(self):
        """При бюджете N — (N+1)-й вызов ждёт пока не освободится слот."""
        mod = _fresh_module()
        # Принудительно устанавливаем бюджет 2 для этого теста
        mod._GLOBAL_SEM = asyncio.Semaphore(2)

        sem = mod.get_global_semaphore()
        entered = []
        released = asyncio.Event()

        async def worker(idx: int):
            async with sem:
                entered.append(idx)
                if len(entered) == 2:
                    released.set()
                await asyncio.sleep(0.05)

        tasks = [asyncio.create_task(worker(i)) for i in range(3)]

        # Первые два должны войти сразу, третий — ждать
        await asyncio.wait_for(released.wait(), timeout=1.0)
        # На этом моменте точно два в семафоре
        self.assertLessEqual(len(entered), 2)

        await asyncio.gather(*tasks)
        # Все три в итоге выполнились
        self.assertEqual(len(entered), 3)


if __name__ == "__main__":
    unittest.main()
