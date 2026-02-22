# -*- coding: utf-8 -*-
"""
Тесты для watchdog-модуля.

Проверяют защиту от шторма самовосстановления:
- повторный self-heal одного и того же компонента блокируется cooldown;
- разные компоненты восстанавливаются независимо.
"""

from unittest.mock import AsyncMock, patch

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


@pytest.mark.asyncio
async def test_gateway_health_requires_fail_streak_before_recovery() -> None:
    """Self-heal по OpenClaw запускается только после порога подряд неуспешных health-check."""
    watchdog = KrabWatchdog()
    # Явно переопределяем started_at чтобы startup grace не блокировал recovery.
    watchdog.started_at = 1000.0
    watchdog.gateway_startup_grace_seconds = 0
    watchdog.gateway_fail_streak_threshold = 3
    # Anti-storm: лимит достаточно большой для этого теста.
    watchdog.max_recovery_attempts = 10
    watchdog._handle_failure = AsyncMock()

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise TimeoutError()

    with (
        patch("src.core.watchdog.time.time", return_value=1000.0),
        patch("src.core.watchdog.aiohttp.ClientSession", return_value=_BrokenSession()),
    ):
        await watchdog._check_gateway_health()
        await watchdog._check_gateway_health()
        await watchdog._check_gateway_health()

    watchdog._handle_failure.assert_awaited_once_with("OpenClawGateway")



@pytest.mark.asyncio
async def test_gateway_health_respects_startup_grace() -> None:
    """Во время startup grace watchdog не должен запускать восстановление OpenClaw."""
    watchdog = KrabWatchdog()
    watchdog.started_at = 1000.0
    watchdog.gateway_startup_grace_seconds = 90
    watchdog._handle_failure = AsyncMock()

    class _BrokenSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise TimeoutError()

    with (
        patch("src.core.watchdog.time.time", return_value=1030.0),
        patch("src.core.watchdog.aiohttp.ClientSession", return_value=_BrokenSession()),
    ):
        await watchdog._check_gateway_health()

    watchdog._handle_failure.assert_not_called()


@pytest.mark.asyncio
async def test_openclaw_failure_uses_targeted_repair_script() -> None:
    """OpenClawGateway должен лечиться targeted-скриптом без принудительного рестарта ядра."""
    watchdog = KrabWatchdog()
    # started_at=0.0 чтобы startup grace был давно в прошлом.
    watchdog.started_at = 0.0
    # Anti-storm: лимит достаточно большой для этого теста.
    watchdog.max_recovery_attempts = 10
    repair_script = "/Users/pablito/Antigravity_AGENTS/Краб/openclaw_runtime_repair.command"

    with (
        patch("src.core.watchdog.time.time", return_value=2000.0),
        patch("src.core.watchdog.os.path.exists", side_effect=lambda path: path == repair_script),
        patch("src.core.watchdog.subprocess.Popen") as popen_mock,
    ):
        await watchdog._handle_failure("OpenClawGateway")

    popen_mock.assert_called_once_with(["/bin/zsh", repair_script])

