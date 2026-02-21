# -*- coding: utf-8 -*-
"""
Тесты для watchdog-модуля.

Проверяют защиту от шторма самовосстановления:
- повторный self-heal одного и того же компонента блокируется cooldown;
- разные компоненты восстанавливаются независимо.
"""

from unittest.mock import patch

import pytest

from src.core.watchdog import KrabWatchdog


@pytest.mark.asyncio
async def test_watchdog_cooldown_blocks_repeated_recovery_for_same_component() -> None:
    """Повторный self-heal для одного компонента в пределах cooldown не запускается."""
    watchdog = KrabWatchdog()
    watchdog.recovery_cooldown_seconds = 180

    with (
        patch("src.core.watchdog.os.path.exists", return_value=True),
        patch("src.core.watchdog.subprocess.Popen") as popen_mock,
        patch("src.core.watchdog.time.time", return_value=1000.0),
    ):
        await watchdog._handle_failure("CoreMainLoop")
        await watchdog._handle_failure("CoreMainLoop")

    assert popen_mock.call_count == 1


@pytest.mark.asyncio
async def test_watchdog_cooldown_is_component_scoped() -> None:
    """Cooldown одного компонента не должен блокировать восстановление другого."""
    watchdog = KrabWatchdog()
    watchdog.recovery_cooldown_seconds = 180

    with (
        patch("src.core.watchdog.os.path.exists", return_value=True),
        patch("src.core.watchdog.subprocess.Popen") as popen_mock,
        patch("src.core.watchdog.time.time", return_value=2000.0),
    ):
        await watchdog._handle_failure("CoreMainLoop")
        await watchdog._handle_failure("OpenClawGateway")

    assert popen_mock.call_count == 2
