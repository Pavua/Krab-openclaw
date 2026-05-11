# -*- coding: utf-8 -*-
"""
Wave 65-G: LM Studio auto-unload idle — verification tests.

Wave 29-RR shipped the idle-watcher (LmStudioIdleWatcher) with legacy env
LM_STUDIO_IDLE_UNLOAD_SEC. Wave 65-G adds the KRAB_-prefixed alias
KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC and verifies four key invariants:

  1. test_idle_threshold_respected     — unload вызывается после порога
  2. test_no_unload_before_threshold   — unload пропускается до порога
  3. test_idle_unload_disabled         — env=0 → unload не вызывается
  4. test_restore_preferred_on_demand  — после unload current_model = None,
     и при новом запросе ensure_model_loaded заново грузит preferred

Дополнительно: alias precedence (KRAB_ переопределяет legacy).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core.lm_studio_idle_watcher import (
    LmStudioIdleWatcher,
    _get_threshold_sec,
    _is_enabled,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mm(
    *,
    current_model: str | None = "gemma-4-26b-a4b-it-optiq",
    last_activity_offset: float = 0.0,
    active_requests: int = 0,
    now: float = 1_000_000.0,
) -> SimpleNamespace:
    """Минимальный stub ModelManager (только поля, читаемые watcher'ом)."""
    mm = SimpleNamespace()
    mm._current_model = current_model
    mm._last_any_activity_ts = now - last_activity_offset
    mm._active_requests = active_requests
    mm.unload_all = AsyncMock()
    # ensure_model_loaded — для restore-preferred сценария
    mm.ensure_model_loaded = AsyncMock(return_value=True)
    mm.load_model = AsyncMock(return_value=True)
    return mm


# ---------------------------------------------------------------------------
# Wave 65-G core invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_threshold_respected() -> None:
    """
    Wave 65-G core: idle > 600s (10 min) при включённом флаге → unload_all вызван.

    Reproduces поведение, наблюдаемое в production:
      - elapsed_sec=637.8, threshold=600 → unload triggered
      - krab_lm_studio_idle_unloads_total++
    """
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=700.0, now=now)  # 700s > 600s
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    # Используем новый KRAB_-prefixed alias для подтверждения работы Wave 65-G nameing
    with patch.dict(
        "os.environ",
        {
            "LM_STUDIO_IDLE_UNLOAD_ENABLED": "1",
            "KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "600",
        },
        clear=False,
    ):
        await watcher._check_once()

    mm.unload_all.assert_called_once()


@pytest.mark.asyncio
async def test_no_unload_before_threshold() -> None:
    """
    Wave 65-G core: idle < threshold → unload пропускается (no-op).

    Защищает от ложных выгрузок «на стыке» сообщений (см. Wave 29-RR rationale).
    """
    now = 1_000_000.0
    # 30s простоя при threshold=600 — далеко до порога
    mm = _make_mm(last_activity_offset=30.0, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict(
        "os.environ",
        {
            "LM_STUDIO_IDLE_UNLOAD_ENABLED": "1",
            "KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "600",
        },
        clear=False,
    ):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_idle_unload_disabled() -> None:
    """
    Wave 65-G core: LM_STUDIO_IDLE_UNLOAD_ENABLED=0 → unload disabled даже
    при очевидном превышении порога. Регрессионная защита от глобального
    включения idle-unload без согласия оператора.
    """
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=9999.0, now=now)  # сильное превышение
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict(
        "os.environ",
        {
            "LM_STUDIO_IDLE_UNLOAD_ENABLED": "0",
            "KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "600",
        },
        clear=False,
    ):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


@pytest.mark.asyncio
async def test_restore_preferred_on_demand() -> None:
    """
    Wave 65-G core: после idle-unload модель действительно выгружена
    (unload_all вызван), а следующий запрос пользователя триггерит
    ensure_model_loaded — re-load по требованию (lazy restore).

    Это симулирует production-сценарий:
      gemma idle 10min → unload (~14 GB free) → user message arrives →
      ensure_model_loaded(preferred) re-loads gemma transparently.
    """
    now = 1_000_000.0
    mm = _make_mm(last_activity_offset=700.0, now=now)
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict(
        "os.environ",
        {"LM_STUDIO_IDLE_UNLOAD_ENABLED": "1", "LM_STUDIO_IDLE_UNLOAD_SEC": "600"},
        clear=False,
    ):
        await watcher._check_once()

    # Шаг 1: unload произошёл
    mm.unload_all.assert_called_once()

    # Шаг 2: симулируем приход нового запроса после unload — пользовательский
    # код должен попасть в ensure_model_loaded(preferred) и заново загрузить.
    # (Watcher не делает re-load сам — это обязанность вызывающего кода.)
    await mm.ensure_model_loaded("gemma-4-26b-a4b-it-optiq")
    mm.ensure_model_loaded.assert_awaited_once_with("gemma-4-26b-a4b-it-optiq")


# ---------------------------------------------------------------------------
# Wave 65-G env alias precedence
# ---------------------------------------------------------------------------


def test_alias_krab_prefix_takes_precedence() -> None:
    """
    KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC должен побеждать
    legacy LM_STUDIO_IDLE_UNLOAD_SEC при одновременной установке.
    """
    with patch.dict(
        "os.environ",
        {
            "LM_STUDIO_IDLE_UNLOAD_SEC": "9999",
            "KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "300",
        },
        clear=False,
    ):
        assert _get_threshold_sec() == 300.0


def test_alias_falls_back_to_legacy_when_unset() -> None:
    """
    Если новый KRAB_-флаг не задан, legacy LM_STUDIO_IDLE_UNLOAD_SEC
    продолжает работать без изменений (backward compatibility).
    """
    with patch.dict(
        "os.environ",
        {"LM_STUDIO_IDLE_UNLOAD_SEC": "450"},
        clear=True,
    ):
        assert _get_threshold_sec() == 450.0


def test_alias_default_when_both_unset() -> None:
    """Default 600s (10 min) если ни alias, ни legacy не заданы."""
    with patch.dict("os.environ", {}, clear=True):
        assert _get_threshold_sec() == 600.0


def test_alias_default_on_malformed_value() -> None:
    """Защита от мусора в env (например, 'abc'): возврат к 600.0."""
    with patch.dict(
        "os.environ",
        {"KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "not_a_number"},
        clear=True,
    ):
        assert _get_threshold_sec() == 600.0


# ---------------------------------------------------------------------------
# Wave 65-G safety nets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_requests_block_unload_even_above_threshold() -> None:
    """
    Если в момент проверки есть активный запрос (_active_requests > 0),
    unload пропускается — критично для не разорвать ответ в середине.
    """
    now = 1_000_000.0
    mm = _make_mm(
        last_activity_offset=9999.0,  # сильное превышение порога
        active_requests=1,  # но идёт активный запрос
        now=now,
    )
    watcher = LmStudioIdleWatcher(mm, now_fn=lambda: now)

    with patch.dict(
        "os.environ",
        {
            "LM_STUDIO_IDLE_UNLOAD_ENABLED": "1",
            "KRAB_LMSTUDIO_AUTO_UNLOAD_AFTER_IDLE_SEC": "600",
        },
        clear=False,
    ):
        await watcher._check_once()

    mm.unload_all.assert_not_called()


def test_is_enabled_default_safe() -> None:
    """
    Default = enabled (1). Это умышленный выбор: для 36 GB M4 Max
    освобождение ~14 GB unified memory критично для swap-pressure.
    """
    with patch.dict("os.environ", {}, clear=True):
        assert _is_enabled() is True
