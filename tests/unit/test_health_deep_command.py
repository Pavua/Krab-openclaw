# -*- coding: utf-8 -*-
"""
Тесты для !health deep — расширенная диагностика Краба (Wave 29-EE).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _health_deep_report, handle_health

# ─── вспомогательные фабрики ────────────────────────────────────────────────

def _make_bot(*, is_owner: bool = True, uptime_sec: int = 3600) -> MagicMock:
    """Минимальный stub KraabUserbot для тестов."""
    import time

    bot = MagicMock()
    bot.me = MagicMock(id=42)
    bot._session_start_time = time.time() - uptime_sec
    bot._session_messages_processed = 100

    access_profile = MagicMock()
    access_profile.level = AccessLevel.OWNER if is_owner else AccessLevel.GUEST
    bot._get_access_profile.return_value = access_profile
    bot._get_command_args.return_value = "deep"
    return bot


def _make_message() -> MagicMock:
    msg = MagicMock()
    msg.reply = AsyncMock()
    msg.edit = AsyncMock()
    msg.from_user = MagicMock(id=99)
    return msg


def _common_patches():
    """Набор стандартных патчей для _health_deep_report без реальных ресурсов."""
    vm_mock = MagicMock()
    vm_mock.total = 32 * 1024**3
    vm_mock.available = 20 * 1024**3
    vm_mock.percent = 37.5

    proc_mock = MagicMock()
    proc_mock.memory_info.return_value = MagicMock(rss=512 * 1024 * 1024)

    return vm_mock, proc_mock


# ─── тесты ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_deep_non_owner_rejected():
    """!health deep отклоняется для non-owner пользователей."""
    bot = _make_bot(is_owner=False)
    message = _make_message()

    with pytest.raises(UserInputError) as exc_info:
        await handle_health(bot, message)

    assert "владельцу" in str(exc_info.value.user_message)


@pytest.mark.asyncio
async def test_health_deep_report_contains_sections():
    """_health_deep_report возвращает строку со всеми ожидаемыми секциями."""
    bot = _make_bot()
    vm_mock, proc_mock = _common_patches()

    with (
        patch(
            "src.handlers.command_handlers.is_lm_studio_available",
            new=AsyncMock(return_value=False),
        ),
        patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        patch("src.handlers.command_handlers.config") as mock_cfg,
        patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="gemini-3-pro"),
        patch("psutil.Process", return_value=proc_mock),
        patch("psutil.virtual_memory", return_value=vm_mock),
        patch("os.getloadavg", return_value=(1.0, 1.5, 2.0)),
        patch("subprocess.run", return_value=MagicMock(stdout="")),
        patch("pathlib.Path.exists", return_value=False),
        patch("src.core.memory_validator.memory_validator") as mock_mv,
        patch("src.core.scheduler.krab_scheduler") as mock_ks,
    ):
        mock_oc.health_check = AsyncMock(return_value=True)
        mock_oc.get_last_runtime_route = MagicMock(return_value={"model": "gemini-3-pro"})
        mock_cfg.LM_STUDIO_URL = "http://localhost:1234"
        mock_cfg.MODEL = "gemini-3-pro"
        mock_mv.list_pending.return_value = []
        mock_ks.list_reminders.return_value = []

        report = await _health_deep_report(bot)

    assert "Health Deep" in report
    assert "Krab process" in report
    assert "OpenClaw" in report
    assert "LM Studio" in report
    assert "Archive.db" in report
    assert "Reminders" in report
    assert "Memory validator" in report
    assert "System" in report


@pytest.mark.asyncio
async def test_health_deep_truncation():
    """Отчёт обрезается до 4000 символов при очень длинных ответах log."""
    bot = _make_bot()
    vm_mock, proc_mock = _common_patches()
    vm_mock.total = 1024**3
    vm_mock.available = 512 * 1024**2
    vm_mock.percent = 50.0
    proc_mock.memory_info.return_value = MagicMock(rss=256 * 1024 * 1024)

    # Лог с 5000 символов SIGTERM (спровоцирует длинный отчёт)
    long_log = "SIGTERM " * 600

    with (
        patch(
            "src.handlers.command_handlers.is_lm_studio_available",
            new=AsyncMock(return_value=False),
        ),
        patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        patch("src.handlers.command_handlers.config") as mock_cfg,
        patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        patch("psutil.Process", return_value=proc_mock),
        patch("psutil.virtual_memory", return_value=vm_mock),
        patch("os.getloadavg", return_value=(0.1, 0.1, 0.1)),
        patch("subprocess.run", return_value=MagicMock(stdout=long_log)),
        patch("pathlib.Path.exists", return_value=False),
        patch("src.core.memory_validator.memory_validator") as mock_mv,
        patch("src.core.scheduler.krab_scheduler") as mock_ks,
    ):
        mock_oc.health_check = AsyncMock(return_value=True)
        mock_oc.get_last_runtime_route = MagicMock(return_value={})
        mock_cfg.LM_STUDIO_URL = "http://localhost:1234"
        mock_cfg.MODEL = "m"
        mock_mv.list_pending.return_value = []
        mock_ks.list_reminders.return_value = []

        report = await _health_deep_report(bot)

    assert len(report) <= 4000


@pytest.mark.asyncio
async def test_health_deep_openclaw_offline():
    """Если OpenClaw недоступен, секция OpenClaw помечается ❌ offline."""
    bot = _make_bot()
    vm_mock, proc_mock = _common_patches()
    vm_mock.total = 1024**3
    vm_mock.available = 512 * 1024**2
    vm_mock.percent = 50.0
    proc_mock.memory_info.return_value = MagicMock(rss=128 * 1024 * 1024)

    with (
        patch(
            "src.handlers.command_handlers.is_lm_studio_available",
            new=AsyncMock(return_value=False),
        ),
        patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        patch("src.handlers.command_handlers.config") as mock_cfg,
        patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        patch("psutil.Process", return_value=proc_mock),
        patch("psutil.virtual_memory", return_value=vm_mock),
        patch("os.getloadavg", return_value=(0.1, 0.1, 0.1)),
        patch("subprocess.run", return_value=MagicMock(stdout="")),
        patch("pathlib.Path.exists", return_value=False),
        patch("src.core.memory_validator.memory_validator") as mock_mv,
        patch("src.core.scheduler.krab_scheduler") as mock_ks,
    ):
        mock_oc.health_check = AsyncMock(return_value=False)
        mock_oc.get_last_runtime_route = MagicMock(return_value={})
        mock_cfg.LM_STUDIO_URL = "http://localhost:1234"
        mock_cfg.MODEL = "m"
        mock_mv.list_pending.return_value = []
        mock_ks.list_reminders.return_value = []

        report = await _health_deep_report(bot)

    assert "❌" in report
    assert "offline" in report


@pytest.mark.asyncio
async def test_health_deep_pending_memory_validator():
    """Если есть pending confirms, секция Memory validator показывает ⚠️ и count."""
    bot = _make_bot()
    vm_mock, proc_mock = _common_patches()
    vm_mock.total = 1024**3
    vm_mock.available = 512 * 1024**2
    vm_mock.percent = 50.0
    proc_mock.memory_info.return_value = MagicMock(rss=128 * 1024 * 1024)

    with (
        patch(
            "src.handlers.command_handlers.is_lm_studio_available",
            new=AsyncMock(return_value=False),
        ),
        patch("src.handlers.command_handlers.openclaw_client") as mock_oc,
        patch("src.handlers.command_handlers.config") as mock_cfg,
        patch("src.handlers.command_handlers.get_runtime_primary_model", return_value="m"),
        patch("psutil.Process", return_value=proc_mock),
        patch("psutil.virtual_memory", return_value=vm_mock),
        patch("os.getloadavg", return_value=(0.1, 0.1, 0.1)),
        patch("subprocess.run", return_value=MagicMock(stdout="")),
        patch("pathlib.Path.exists", return_value=False),
        patch("src.core.memory_validator.memory_validator") as mock_mv,
        patch("src.core.scheduler.krab_scheduler") as mock_ks,
    ):
        mock_oc.health_check = AsyncMock(return_value=True)
        mock_oc.get_last_runtime_route = MagicMock(return_value={"model": "m"})
        mock_cfg.LM_STUDIO_URL = "http://localhost:1234"
        mock_cfg.MODEL = "m"
        # 3 pending подтверждения
        mock_mv.list_pending.return_value = [MagicMock(), MagicMock(), MagicMock()]
        mock_ks.list_reminders.return_value = []

        report = await _health_deep_report(bot)

    assert "⚠️" in report
    # Pending !confirm: 3
    assert "Pending !confirm: 3" in report
