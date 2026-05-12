# -*- coding: utf-8 -*-
"""Wave 133: LM Studio registry probe metrics.

Экспонируем visibility "какие модели сейчас загружены в LM Studio и сколько
RAM это занимает". Дополняет Wave 65-G (idle unload) и Wave 86
(pressure-aware fallback) — те реагируют, эта *показывает*.

Метрики:
    krab_lm_models_loaded_count  — gauge, число загруженных моделей
    krab_lm_estimated_ram_gb     — gauge, оценка суммарного RAM-footprint (ГБ)

prometheus_client soft-import — модуль безопасен при отсутствии зависимости.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Gauge as _Gauge  # type: ignore[import-not-found]

    krab_lm_models_loaded_count: Any = _Gauge(
        "krab_lm_models_loaded_count",
        "LM Studio: число загруженных моделей (Wave 133)",
    )
    krab_lm_estimated_ram_gb: Any = _Gauge(
        "krab_lm_estimated_ram_gb",
        "LM Studio: оценка суммарного RAM в ГБ для загруженных моделей (Wave 133)",
    )
except Exception:  # noqa: BLE001
    krab_lm_models_loaded_count = None
    krab_lm_estimated_ram_gb = None


def set_lm_registry_state(*, loaded_count: int, estimated_ram_gb: float) -> None:
    """Записать текущий snapshot. Fail-safe."""
    try:
        if krab_lm_models_loaded_count is not None:
            krab_lm_models_loaded_count.set(max(0, int(loaded_count)))
        if krab_lm_estimated_ram_gb is not None:
            krab_lm_estimated_ram_gb.set(max(0.0, float(estimated_ram_gb)))
    except Exception:  # noqa: BLE001
        pass
