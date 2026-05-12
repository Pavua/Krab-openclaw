# -*- coding: utf-8 -*-
"""Wave 115: Prometheus метрики cron run history.

- Counter `krab_cron_run_total{label, exit_class}` — increments per wrapper run.
  exit_class — "ok" если exit_code == 0, иначе "fail".
- Histogram `krab_cron_duration_seconds{label}` — длительность каждого запуска.

prometheus_client опционален: при отсутствии все объекты None, helper'ы no-op.
"""

from __future__ import annotations

from typing import Any

krab_cron_run_total: Any = None
krab_cron_duration_seconds: Any = None

try:
    from prometheus_client import Counter as _Counter  # type: ignore[import-not-found]
    from prometheus_client import Histogram as _Histogram  # type: ignore[import-not-found]

    krab_cron_run_total = _Counter(
        "krab_cron_run_total",
        "Cron wrapper invocations с разбивкой по label и exit_class (ok/fail)",
        ["label", "exit_class"],
    )
    krab_cron_duration_seconds = _Histogram(
        "krab_cron_duration_seconds",
        "Длительность одного cron-запуска (секунды) с разбивкой по label",
        ["label"],
        # Корзины ориентированы на типичные LaunchAgent'ы Krab: от подсекунды
        # (быстрые health-check'и) до многоминутных (audit/digest).
        buckets=(0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0),
    )
except Exception:  # noqa: BLE001
    krab_cron_run_total = None
    krab_cron_duration_seconds = None


def _classify(exit_code: int) -> str:
    return "ok" if int(exit_code) == 0 else "fail"


def record_run(*, label: str, exit_code: int, duration_sec: float) -> None:
    """Одновременно обновляет counter и histogram. Best-effort."""
    label_s = (label or "unknown").strip() or "unknown"
    exit_class = _classify(exit_code)
    if krab_cron_run_total is not None:
        try:
            krab_cron_run_total.labels(label=label_s, exit_class=exit_class).inc()
        except Exception:  # noqa: BLE001
            pass
    if krab_cron_duration_seconds is not None:
        try:
            krab_cron_duration_seconds.labels(label=label_s).observe(max(0.0, float(duration_sec)))
        except Exception:  # noqa: BLE001
            pass
