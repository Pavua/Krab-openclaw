# -*- coding: utf-8 -*-
"""
Тесты для !health deep — расширенная диагностика Краба (Wave 29-EE/FF).

После Wave 29-FF логика сбора перенесена в collect_health_deep().
_health_deep_report теперь форматирует dict → markdown.
Тесты патчат collect_health_deep в его собственном модуле.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.access_control import AccessLevel
from src.core.exceptions import UserInputError
from src.handlers.command_handlers import _health_deep_report, handle_health

# Патчим collect_health_deep в его собственном модуле (local import внутри _health_deep_report
# делает `from ..core.health_deep_collector import collect_health_deep`, поэтому
# патч должен быть на объект в этом модуле).
_MOCK_PATH = "src.core.health_deep_collector.collect_health_deep"


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


def _base_data(**overrides) -> dict:
    """Базовый dict, который возвращает collect_health_deep в mock."""
    base: dict = {
        "krab": {"uptime_sec": 3600, "rss_mb": 512, "cpu_pct": 1.0},
        "openclaw": {"healthy": True, "last_route": {"model": "gemini-3-pro"}},
        "lm_studio": {"state": "offline", "active_model": None},
        "archive_db": {"integrity": "missing", "orphan_fts5": 0, "orphan_vec": 0},
        "reminders": {"pending": 0},
        "memory_validator": {"pending_confirm": 0},
        "sigterm_recent_count": 0,
        "system": {
            "load_avg": [1.0, 1.5, 2.0],
            "free_mb": 20480,
            "total_mb": 32768,
            "used_pct": 37.5,
        },
    }
    base.update(overrides)
    return base


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

    with patch(_MOCK_PATH, new=AsyncMock(return_value=_base_data())):
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
    """Отчёт обрезается до 4000 символов при длинных данных."""
    bot = _make_bot()

    # Длинное имя модели спровоцирует большой отчёт
    data = _base_data()
    data["openclaw"] = {
        "healthy": True,
        "last_route": {"model": "g" * 3000},
    }

    with patch(_MOCK_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert len(report) <= 4000


@pytest.mark.asyncio
async def test_health_deep_openclaw_offline():
    """Если OpenClaw недоступен, секция OpenClaw помечается ❌ offline."""
    bot = _make_bot()
    data = _base_data()
    data["openclaw"] = {"healthy": False, "last_route": {"model": "m"}}

    with patch(_MOCK_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "❌" in report
    assert "offline" in report


@pytest.mark.asyncio
async def test_health_deep_pending_memory_validator():
    """Если есть pending confirms, секция Memory validator показывает ⚠️ и count."""
    bot = _make_bot()
    data = _base_data()
    data["memory_validator"] = {"pending_confirm": 3}

    with patch(_MOCK_PATH, new=AsyncMock(return_value=data)):
        report = await _health_deep_report(bot)

    assert "⚠️" in report
    assert "Pending !confirm: 3" in report
