# -*- coding: utf-8 -*-
"""
Sliding-window хранилище RRF scores для memory retrieval.

Thread-safe bounded deque (макс. 1000 записей), отдаёт percentiles
p50/p90/p95/p99 для Prometheus gauge метрик.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Sequence

_MAX_SIZE = 1000


class _ScoreWindow:
    """Thread-safe ограниченный буфер RRF scores."""

    def __init__(self, maxlen: int = _MAX_SIZE) -> None:
        self._maxlen = maxlen
        self._lock = threading.Lock()
        self._buf: deque[float] = deque(maxlen=maxlen)

    # ------------------------------------------------------------------
    # Запись.
    # ------------------------------------------------------------------

    def record(self, scores: Sequence[float]) -> None:
        """Добавить список scores; автоматически вытесняет старые при переполнении."""
        if not scores:
            return
        with self._lock:
            self._buf.extend(scores)

    # ------------------------------------------------------------------
    # Чтение.
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)

    def percentiles(self) -> dict[str, float]:
        """Возвращает p50/p90/p95/p99 или пустой dict если нет данных."""
        with self._lock:
            if not self._buf:
                return {}
            data = sorted(self._buf)

        n = len(data)

        def _pct(p: float) -> float:
            # Nearest-rank method.
            idx = max(0, min(n - 1, int(p / 100.0 * n + 0.5) - 1))
            return data[idx]

        return {
            "p50": _pct(50),
            "p90": _pct(90),
            "p95": _pct(95),
            "p99": _pct(99),
        }

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()


# Глобальный singleton.
rrf_score_window = _ScoreWindow()


def record_scores(scores: list[float]) -> None:
    """Shortcut для вызова из HybridRetriever.search()."""
    rrf_score_window.record(scores)
