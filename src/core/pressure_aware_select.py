# -*- coding: utf-8 -*-
"""
Wave 86: memory-pressure-aware model selection.

При нехватке свободной RAM (M4 Max 36 GB unified) переключаем local LM Studio
модель на меньшую или форсируем cloud, чтобы не уйти в swap (Wave 65-E порог
22/32 GB) и не убить Whisper Large + Gemma одновременно.

Pre-filter перед стандартным выбором ModelRouter / provider_manager: если
free_memory_gb < SOFT_THRESHOLD — предпочитаем самую маленькую local модель;
если < HARD_THRESHOLD — local запрещён, fallback на cloud.

Env-gate: KRAB_PRESSURE_AWARE_SELECTION=1 (default ON, Wave 217 — production).
Установка "0"/"false" полностью обходит pre-filter.

Wave 217 safety: если pressure detection срабатывает > MAX_FALLBACKS_PER_HOUR
(default 10) подряд за час — авто-выключаем pre-filter и шлём Sentry warning.
Это защищает от runaway-loop при misfiring (например, при поломке psutil
или нестабильном vm_stat). Перезапуск процесса сбрасывает auto-disable.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from typing import Deque, Iterable, Optional

import structlog

from .prometheus_metrics import (
    inc_pressure_aware_fallback,
)

logger = structlog.get_logger(__name__)

# Пороги в GB. SOFT — предпочитаем меньшую local модель. HARD — local запрещён.
SOFT_PRESSURE_GB = 4.0
HARD_PRESSURE_GB = 2.0

_CLOUD_SENTINEL = "__cloud__"

# Wave 217: runtime safety — auto-disable при шквале fallback'ов.
# Сторожевое окно — 1 час; лимит — 10 fallback'ов в окне.
MAX_FALLBACKS_PER_HOUR = 10
SAFETY_WINDOW_SECONDS = 3600.0

# Глобальное in-process состояние safety guard. Лочим словарь, чтобы
# многопоточные вызовы (pyrogram update handlers + background tasks)
# не гонялись за deque/флагом.
_safety_lock = threading.Lock()
_fallback_timestamps: Deque[float] = deque()
_auto_disabled: bool = False
_auto_disabled_at: Optional[float] = None
_auto_disable_reason: Optional[str] = None


def _env_enabled() -> bool:
    """Возвращает True если KRAB_PRESSURE_AWARE_SELECTION включён (default ON)."""
    raw = os.getenv("KRAB_PRESSURE_AWARE_SELECTION", "1").strip().lower()
    return raw not in ("0", "false", "no", "off", "")


def _safety_guard_active() -> bool:
    """Wave 217: True если runtime safety auto-disabled pre-filter."""
    with _safety_lock:
        return _auto_disabled


def reset_safety_guard() -> None:
    """Wave 217: сброс state guard (для тестов и /admin reset)."""
    global _auto_disabled, _auto_disabled_at, _auto_disable_reason
    with _safety_lock:
        _fallback_timestamps.clear()
        _auto_disabled = False
        _auto_disabled_at = None
        _auto_disable_reason = None


def _record_fallback_and_check_safety(*, from_model: str, to_model: str, reason: str) -> None:
    """
    Wave 217: фиксируем fallback в скользящем окне и при превышении лимита
    включаем auto-disable + шлём Sentry warning. Best-effort — никогда
    не бросаем наружу.
    """
    global _auto_disabled, _auto_disabled_at, _auto_disable_reason

    now = time.monotonic()
    trip = False
    count_in_window = 0

    with _safety_lock:
        if _auto_disabled:
            return
        _fallback_timestamps.append(now)
        # выкидываем устаревшие записи из окна
        cutoff = now - SAFETY_WINDOW_SECONDS
        while _fallback_timestamps and _fallback_timestamps[0] < cutoff:
            _fallback_timestamps.popleft()
        count_in_window = len(_fallback_timestamps)
        if count_in_window > MAX_FALLBACKS_PER_HOUR:
            _auto_disabled = True
            _auto_disabled_at = now
            _auto_disable_reason = (
                f"runaway: {count_in_window} fallbacks in {SAFETY_WINDOW_SECONDS:.0f}s"
            )
            trip = True

    if not trip:
        return

    # Логирование + Sentry — вне лока, чтобы не блокировать другие потоки.
    logger.error(
        "pressure_aware_auto_disabled",
        count=count_in_window,
        window_seconds=SAFETY_WINDOW_SECONDS,
        last_from=from_model,
        last_to=to_model,
        last_reason=reason,
    )
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            "Wave 217: pressure_aware_select auto-disabled (runaway fallbacks)",
            level="warning",
        )
    except Exception:  # noqa: BLE001 — sentry опционален
        pass


def get_free_memory_gb() -> Optional[float]:
    """
    Возвращает свободную RAM в GiB.

    Предпочитает psutil.virtual_memory().available (учитывает inactive+free).
    Fallback на macOS vm_stat если psutil недоступен. None если оба метода
    failed — caller трактует как "неизвестно, пропускаем pre-filter".
    """
    try:
        import psutil  # type: ignore[import-not-found]

        available = psutil.virtual_memory().available
        return round(float(available) / (1024**3), 2)
    except Exception as exc:  # noqa: BLE001 - psutil опционален
        logger.debug(
            "pressure_aware_psutil_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # Fallback на vm_stat (macOS). Парсим "Pages free" + "Pages inactive".
    try:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if result.returncode != 0:
            return None
        page_size = 4096
        free_pages = 0
        inactive_pages = 0
        for line in result.stdout.splitlines():
            if line.startswith("Mach Virtual Memory Statistics"):
                continue
            if "page size of" in line:
                parts = line.split()
                for i, tok in enumerate(parts):
                    if tok.isdigit():
                        page_size = int(tok)
                        break
            elif line.startswith("Pages free:"):
                free_pages = int(line.split(":")[1].strip().rstrip("."))
            elif line.startswith("Pages inactive:"):
                inactive_pages = int(line.split(":")[1].strip().rstrip("."))
        available_bytes = (free_pages + inactive_pages) * page_size
        return round(available_bytes / (1024**3), 2)
    except Exception as exc:  # noqa: BLE001 - vm_stat parsing best-effort
        logger.debug(
            "pressure_aware_vmstat_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _is_local_model(model_id: str) -> bool:
    """Определяет local-модель по имени (LM Studio convention)."""
    low = model_id.lower()
    return (
        low == "local" or "lm_studio" in low or "lmstudio" in low or "mlx" in low or "local/" in low
    )


def pressure_aware_model_select(
    preferred_model: str,
    candidate_models: Iterable[dict],
    *,
    free_gb_override: Optional[float] = None,
    cloud_fallback: str = _CLOUD_SENTINEL,
) -> str:
    """
    Pre-filter для model selection с учётом memory pressure.

    Args:
        preferred_model: текущий выбор (как было бы без pre-filter).
        candidate_models: список моделей {id, size_gb?, size_bytes?} —
            обычно вывод local_health._normalize_lm_models() + cloud entries.
        free_gb_override: тестовый override для inject clock-style.
        cloud_fallback: ID cloud-модели для HARD pressure case (default
            sentinel "__cloud__" — caller должен распознать).

    Returns:
        model_id для использования. preferred_model если pre-filter bypass.
    """
    if not _env_enabled():
        return preferred_model

    # Wave 217: runtime safety — если guard сработал, обходим pre-filter
    # (до перезапуска процесса).
    if _safety_guard_active():
        return preferred_model

    free_gb = free_gb_override if free_gb_override is not None else get_free_memory_gb()
    if free_gb is None:
        # Не удалось измерить — не блокируем выбор
        return preferred_model

    if free_gb >= SOFT_PRESSURE_GB:
        return preferred_model

    preferred_is_local = _is_local_model(preferred_model)

    # HARD pressure (< 2 GB): local запрещён, форсируем cloud
    if free_gb < HARD_PRESSURE_GB:
        if preferred_is_local:
            logger.warning(
                "pressure_aware_hard_fallback",
                free_gb=free_gb,
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="hard_pressure",
            )
            inc_pressure_aware_fallback(
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="hard_pressure",
            )
            _record_fallback_and_check_safety(
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="hard_pressure",
            )
            return cloud_fallback
        # preferred уже cloud — оставляем
        return preferred_model

    # SOFT pressure (2-4 GB): если preferred local — берём самую маленькую local
    if preferred_is_local:
        local_candidates: list[tuple[float, str]] = []
        for m in candidate_models:
            mid = m.get("id") or m.get("key") or ""
            if not mid or not _is_local_model(mid):
                continue
            size = m.get("size_gb")
            if size is None and m.get("size_bytes"):
                try:
                    size = float(m["size_bytes"]) / (1024**3)
                except (TypeError, ValueError):
                    size = None
            if size is None or size <= 0:
                continue
            local_candidates.append((float(size), mid))

        if local_candidates:
            local_candidates.sort()
            smallest = local_candidates[0][1]
            if smallest != preferred_model:
                logger.info(
                    "pressure_aware_soft_fallback",
                    free_gb=free_gb,
                    from_model=preferred_model,
                    to_model=smallest,
                    reason="soft_pressure",
                )
                inc_pressure_aware_fallback(
                    from_model=preferred_model,
                    to_model=smallest,
                    reason="soft_pressure",
                )
                _record_fallback_and_check_safety(
                    from_model=preferred_model,
                    to_model=smallest,
                    reason="soft_pressure",
                )
                return smallest
        else:
            # Нет local candidates с известным размером — fallback на cloud
            logger.warning(
                "pressure_aware_soft_no_local_candidates",
                free_gb=free_gb,
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="soft_no_candidates",
            )
            inc_pressure_aware_fallback(
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="soft_no_candidates",
            )
            _record_fallback_and_check_safety(
                from_model=preferred_model,
                to_model=cloud_fallback,
                reason="soft_no_candidates",
            )
            return cloud_fallback

    return preferred_model
