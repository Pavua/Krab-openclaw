# -*- coding: utf-8 -*-
import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock, ANY
from src.core.watchdog import KrabWatchdog

@pytest.fixture
def watchdog():
    wd = KrabWatchdog()
    wd.router = MagicMock()
    wd.router.unload_models_manual = AsyncMock(return_value=True)
    wd._handle_failure = AsyncMock()
    wd.notifier = MagicMock()
    wd.notifier.notify_system = AsyncMock()
    return wd

@pytest.mark.asyncio
async def test_watchdog_ram_low(watchdog):
    """RAM в норме -> ничего не происходит."""
    with patch("psutil.virtual_memory") as mock_mem:
        mock_mem.return_value.percent = 70.0
        await watchdog._check_resources()
        
        watchdog.router.unload_models_manual.assert_not_called()
        watchdog._handle_failure.assert_not_called()

@pytest.mark.asyncio
async def test_watchdog_ram_soft_heal(watchdog):
    """RAM > 90% -> Soft healing (unload models) срабатывает, но хард-рестарта нет."""
    with patch("psutil.virtual_memory") as mock_mem:
        # Сначала 92%, потом после выгрузки 85%
        mock_mem.return_value.percent = 92.0
        
        # Чтобы второй вызов psutil (после sleep) вернул 85
        mock_mem.side_effect = [
            MagicMock(percent=92.0),
            MagicMock(percent=85.0)
        ]
        
        await watchdog._check_resources()
        
        watchdog.router.unload_models_manual.assert_awaited_once()
        watchdog._handle_failure.assert_not_called()
        watchdog.notifier.notify_system.assert_awaited_with(
            "SOFT HEALING TRIGGERED", 
            ANY
        )

@pytest.mark.asyncio
async def test_watchdog_ram_hard_heal(watchdog):
    """RAM > 90% -> Soft healing не помог (осталось > 95%) -> Hard healing."""
    with patch("psutil.virtual_memory") as mock_mem:
        # Сначала 96%, потом после выгрузки все еще 96%
        mock_mem.side_effect = [
            MagicMock(percent=96.0),
            MagicMock(percent=97.0)
        ]
        
        await watchdog._check_resources()
        
        watchdog.router.unload_models_manual.assert_awaited_once()
        watchdog._handle_failure.assert_awaited_with("CriticalResourcePressure")

if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__]))
