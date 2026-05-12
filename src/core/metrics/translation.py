# -*- coding: utf-8 -*-
"""
Prometheus метрики content-hash translation cache (Wave 95).

Live counters/gauges используются `translation_cache.TranslationCache` —
инкрементируются на каждое `lookup()` / `store()`. Метрики собираются
основным `prometheus_metrics.collect_metrics()` через `prometheus_client`
default REGISTRY, дополнительных шагов регистрации не нужно.

Если `prometheus_client` недоступен (unit-тесты в slim env), используем
no-op заглушки чтобы импорт не падал.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-not-found]

    _HAS_PROM = True
except Exception:  # pragma: no cover - slim env без prometheus_client

    class _Noop:
        def labels(self, *_a: Any, **_kw: Any) -> "_Noop":
            return self

        def inc(self, *_a: Any, **_kw: Any) -> None:
            return None

        def set(self, *_a: Any, **_kw: Any) -> None:
            return None

    Counter = Gauge = _Noop  # type: ignore[assignment,misc]
    _HAS_PROM = False


# Counter cache hits — инкрементируется на каждый success lookup.
krab_translation_cache_hits_total = Counter(
    "krab_translation_cache_hits_total",
    "Content-hash translation cache hits (Wave 95)",
)

# Counter cache misses — пустой lookup или истёкший entry.
krab_translation_cache_misses_total = Counter(
    "krab_translation_cache_misses_total",
    "Content-hash translation cache misses (Wave 95)",
)

# Gauge текущего размера cache — обновляется при store/evict.
krab_translation_cache_size = Gauge(
    "krab_translation_cache_size",
    "Текущее число entries в content-hash translation cache (Wave 95)",
)


__all__ = [
    "krab_translation_cache_hits_total",
    "krab_translation_cache_misses_total",
    "krab_translation_cache_size",
]
