# -*- coding: utf-8 -*-
"""
llm_latency_tracker — накопитель LLM latency для Prometheus histogram.

Используется openclaw_client при каждом завершённом LLM-вызове:
    from src.core.llm_latency_tracker import llm_latency_tracker
    llm_latency_tracker.observe(provider="google", model="gemini-3-pro", duration_s=1.23)

prometheus_metrics.collect_metrics() читает текущий snapshot и выдаёт
Prometheus histogram (bucket/sum/count) без сторонних библиотек.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

# Стандартные bucket-границы (секунды), совместимые с Prometheus defaults
_DEFAULT_BUCKETS: tuple[float, ...] = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf"))


@dataclass
class _SeriesData:
    """Накопленные данные одной {provider, model} series."""

    buckets: dict[float, int] = field(default_factory=dict)
    total_sum: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        for b in _DEFAULT_BUCKETS:
            self.buckets[b] = 0


class LLMLatencyTracker:
    """Thread-safe накопитель LLM-latency histogram по (provider, model)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # ключ: (provider, model) → данные
        self._series: dict[tuple[str, str], _SeriesData] = defaultdict(_SeriesData)

    def observe(self, provider: str, model: str, duration_s: float) -> None:
        """Записать одно наблюдение (duration в секундах).

        Args:
            provider: имя провайдера ("google", "openai", "local", …)
            model:    идентификатор модели
            duration_s: длительность LLM-запроса в секундах
        """
        key = (str(provider)[:60], str(model)[:80])
        with self._lock:
            s = self._series[key]
            for b in _DEFAULT_BUCKETS:
                if duration_s <= b:
                    s.buckets[b] += 1
            s.total_sum += duration_s
            s.count += 1

    def snapshot(self) -> list[dict]:
        """Вернуть снимок всех series.

        Returns:
            Список dict с ключами:
            - provider, model
            - buckets: {le_str: count}  (le="0.1", …, le="+Inf")
            - sum, count
        """
        result = []
        with self._lock:
            for (provider, model), s in self._series.items():
                buckets_out: dict[str, int] = {}
                for b, cnt in sorted(s.buckets.items()):
                    le_str = "+Inf" if b == float("inf") else str(b)
                    buckets_out[le_str] = cnt
                result.append(
                    {
                        "provider": provider,
                        "model": model,
                        "buckets": buckets_out,
                        "sum": s.total_sum,
                        "count": s.count,
                    }
                )
        return result

    def reset(self) -> None:
        """Сброс всех накопленных данных (тесты / maintenance)."""
        with self._lock:
            self._series.clear()


# Синглтон
llm_latency_tracker = LLMLatencyTracker()
