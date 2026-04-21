# -*- coding: utf-8 -*-
"""
Тесты для src.core.memory_retrieval_scores — sliding-window RRF scores.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.core.memory_retrieval_scores import _ScoreWindow, record_scores, rrf_score_window


# ---------------------------------------------------------------------------
# Bounded buffer.
# ---------------------------------------------------------------------------

class TestBounded:
    def test_bounded_at_maxlen(self):
        w = _ScoreWindow(maxlen=1000)
        # Добавляем 2000 записей.
        w.record(list(range(2000)))
        assert len(w) == 1000

    def test_bounded_exact_maxlen(self):
        w = _ScoreWindow(maxlen=5)
        w.record([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        assert len(w) == 5

    def test_empty_returns_zero(self):
        w = _ScoreWindow(maxlen=100)
        assert len(w) == 0

    def test_clear(self):
        w = _ScoreWindow(maxlen=10)
        w.record([1.0, 2.0, 3.0])
        w.clear()
        assert len(w) == 0

    def test_empty_record_noop(self):
        w = _ScoreWindow(maxlen=10)
        w.record([])
        assert len(w) == 0


# ---------------------------------------------------------------------------
# Percentiles — known distributions.
# ---------------------------------------------------------------------------

class TestPercentiles:
    def test_empty_returns_empty_dict(self):
        w = _ScoreWindow(maxlen=100)
        assert w.percentiles() == {}

    def test_uniform_distribution(self):
        """Для [0, 1, 2, ..., 99] p50≈49, p90≈89, p95≈94, p99≈98."""
        w = _ScoreWindow(maxlen=200)
        w.record([float(i) for i in range(100)])
        p = w.percentiles()
        assert set(p.keys()) == {"p50", "p90", "p95", "p99"}
        # Проверяем грубые диапазоны (nearest-rank может сдвигать на 1).
        assert 48.0 <= p["p50"] <= 51.0
        assert 88.0 <= p["p90"] <= 91.0
        assert 93.0 <= p["p95"] <= 96.0
        assert 97.0 <= p["p99"] <= 100.0

    def test_single_value(self):
        w = _ScoreWindow(maxlen=10)
        w.record([0.42])
        p = w.percentiles()
        assert p["p50"] == pytest.approx(0.42)
        assert p["p99"] == pytest.approx(0.42)

    def test_all_same_value(self):
        w = _ScoreWindow(maxlen=50)
        w.record([0.75] * 50)
        p = w.percentiles()
        for v in p.values():
            assert v == pytest.approx(0.75)

    def test_two_extreme_values(self):
        """p50 должен быть либо 0.0 либо 1.0 (nearest-rank на 2 элементах)."""
        w = _ScoreWindow(maxlen=10)
        w.record([0.0, 1.0])
        p = w.percentiles()
        assert p["p99"] == pytest.approx(1.0)
        assert p["p50"] in (0.0, 1.0)

    def test_rrf_typical_range(self):
        """RRF scores обычно в [1/(60+N) .. 1/61] — тест с реалистичными данными."""
        k = 60
        scores = [1.0 / (k + rank) for rank in range(1, 41)]  # 40 результатов
        w = _ScoreWindow(maxlen=100)
        w.record(scores)
        p = w.percentiles()
        # p50 должен быть в середине диапазона.
        assert p["p50"] > 0.0
        assert p["p99"] <= scores[0] + 1e-9  # не превышает максимум


# ---------------------------------------------------------------------------
# Thread safety.
# ---------------------------------------------------------------------------

class TestThreadSafe:
    def test_concurrent_writes(self):
        w = _ScoreWindow(maxlen=1000)
        errors: list[Exception] = []

        def writer(start: float, n: int) -> None:
            try:
                for i in range(n):
                    w.record([start + i * 0.001])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(float(t), 200)) for t in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors, f"Thread errors: {errors}"
        # Буфер не может быть больше maxlen.
        assert len(w) <= 1000

    def test_concurrent_read_write(self):
        w = _ScoreWindow(maxlen=500)
        stop = threading.Event()
        errors: list[Exception] = []

        def writer() -> None:
            try:
                while not stop.is_set():
                    w.record([0.5, 0.6, 0.7])
            except Exception as exc:
                errors.append(exc)

        def reader() -> None:
            try:
                for _ in range(100):
                    p = w.percentiles()
                    # percentiles() должен возвращать dict или пустой dict
                    assert isinstance(p, dict)
            except Exception as exc:
                errors.append(exc)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_read.join(timeout=5)
        stop.set()
        t_write.join(timeout=2)

        assert not errors, f"Thread errors: {errors}"

    def test_concurrent_len(self):
        w = _ScoreWindow(maxlen=100)
        errors: list[Exception] = []

        def work() -> None:
            try:
                for _ in range(50):
                    w.record([0.1, 0.2])
                    _ = len(w)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert not errors


# ---------------------------------------------------------------------------
# Глобальный singleton + record_scores().
# ---------------------------------------------------------------------------

class TestGlobalSingleton:
    def test_record_scores_shortcut(self):
        rrf_score_window.clear()
        record_scores([0.1, 0.2, 0.3])
        assert len(rrf_score_window) == 3

    def test_global_singleton_bounded(self):
        rrf_score_window.clear()
        record_scores(list(range(1500)))
        assert len(rrf_score_window) == 1000
