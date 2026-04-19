# -*- coding: utf-8 -*-
"""Тесты LmStudioIdleWatcher: idle-unload логика, env-gate, counter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.lm_studio_idle_watcher import (
    LmStudioIdleWatcher,
    _get_threshold_sec,
    _is_enabled,
    get_idle_unloads_total,
)

# ---------------------------------------------------------------------------
# Вспомогательный stub ModelManager
# ---------------------------------------------------------------------------

def _make_mm(
    *,
    current_model: str | None = "test-model",
    last_activity_offset: float = 0.0,
    active_requests: int = 0,
    now: float = 1_000_000.0,
) -> SimpleNamespace:  # type: ignore[return]
    """Создаёт минимальный stub ModelManager."""
    mm = SimpleNamespace()
    mm._current_model = current_model
    mm._last_any_activity_ts = now - last_activity_offset
    mm._active_requests = active_requests
    mm.unload_all = AsyncMock()
    return mm


# ---------------------------------------------------------------------------
# Тесты env-gate
# ---------------------------------------------------------------------------

def test_is_enabled_default() -> None:
    with patch.dict("os.environ", {}, clear=True):
        # По умолчанию (нет переменной) → enabled
        assert _is_enabled() is True


def test_is_enabled_explicit_zero() -> None:
    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "0"}):
        assert _is_enabled() is False


def test_is_enabled_explicit_one() -> None:
    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1"}):
        assert _is_enabled() is True


def test_get_threshold_sec_default() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert _get_threshold_sec() == 600.0


def test_get_threshold_sec_custom() -> None:
    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_SEC": "300"}):
        assert _get_threshold_sec() == 300.0


# ---------------------------------------------------------------------------
# Основные тесты _check_once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_unload_when_disabled() -> None:
    """env disabled → unload_all не вызывается даже при долгом idle."""
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=9999.0, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "0"}):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_no_unload_when_activity_fresh() -> None:
    """Активность была недавно → unload не запускается."""
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=30.0, now=now)  # 30s < 600s threshold
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "600"}):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_unload_triggered_when_idle_exceeded() -> None:
    """Idle > threshold при enabled → unload_all вызывается, counter растёт."""
    import src.core.lm_studio_idle_watcher as mod

    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=700.0, now=now)  # 700s > 600s
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    before = mod._idle_unloads_total
    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "600"}):
        await watcher._check_once()

    mm.unload_all.assert_called_once()
    assert mod._idle_unloads_total == before + 1


@pytest.mark.asyncio
async def test_no_unload_when_active_requests() -> None:
    """Есть активные запросы → unload пропускается даже при долгом idle."""
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=700.0, active_requests=2, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "600"}):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_no_unload_when_model_already_none() -> None:
    """Модель уже выгружена (_current_model=None) → skip, unload_all не вызывается."""
    now = 1_000_000.0
    mm = _make_mm(current_model=None, last_activity_offset=9999.0, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "60"}):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_counter_accessible_via_get_idle_unloads_total() -> None:
    """get_idle_unloads_total() возвращает актуальное значение счётчика."""
    import src.core.lm_studio_idle_watcher as mod

    before = mod._idle_unloads_total
    assert get_idle_unloads_total() == before

    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=700.0, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "600"}):
        await watcher._check_once()

    assert get_idle_unloads_total() == before + 1


@pytest.mark.asyncio
async def test_start_stop_task_lifecycle() -> None:
    """start() создаёт task, stop() отменяет его (task становится done)."""
    now = 1_000_000.0
    mm = _make_mm(current_model=None, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict("os.environ", {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "0"}):
        watcher.start()
        assert watcher._task is not None
        assert not watcher._task.done()

        watcher.stop()
        # Даём asyncio обработать отмену
        await asyncio.sleep(0)
        assert watcher._task.done()
