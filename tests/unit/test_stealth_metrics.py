# -*- coding: utf-8 -*-
"""Тесты для src.core.stealth_metrics."""
from __future__ import annotations

import threading

import pytest

from src.core import stealth_metrics
from src.core.stealth_metrics import get_counts, record_detection, reset


@pytest.fixture(autouse=True)
def _clean():
    """Сброс счётчиков до и после каждого теста."""
    reset()
    yield
    reset()


def test_record_detection_increments():
    record_detection("canvas")
    record_detection("canvas")
    record_detection("webgl")
    counts = get_counts()
    assert counts["canvas"] == 2
    assert counts["webgl"] == 1


def test_record_detection_new_layer():
    record_detection("captcha")
    assert get_counts()["captcha"] == 1


def test_get_counts_returns_copy():
    record_detection("webrtc")
    copy = get_counts()
    copy["webrtc"] = 999  # изменение копии не должно влиять на внутренний счётчик
    assert get_counts()["webrtc"] == 1


def test_reset_clears():
    record_detection("blocked")
    record_detection("ratelimit")
    reset()
    assert get_counts() == {}


def test_concurrent_updates_safe():
    """50 потоков по 100 инкрементов = 5000 итого."""
    N_THREADS = 50
    N_INC = 100

    def worker():
        for _ in range(N_INC):
            record_detection("concurrent_layer")

    threads = [threading.Thread(target=worker) for _ in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert get_counts()["concurrent_layer"] == N_THREADS * N_INC


def test_prometheus_output_contains_stealth_metric():
    """collect_metrics() должен содержать krab_stealth_detection_total{layer=...}."""
    record_detection("canvas")
    record_detection("webrtc")

    from src.core.prometheus_metrics import collect_metrics

    output = collect_metrics()
    assert 'krab_stealth_detection_total{layer="canvas"}' in output
    assert 'krab_stealth_detection_total{layer="webrtc"}' in output


def test_prometheus_output_empty_when_no_detections():
    """Если счётчики пусты — строки с krab_stealth_detection_total не должно быть."""
    from src.core.prometheus_metrics import collect_metrics

    output = collect_metrics()
    assert "krab_stealth_detection_total" not in output
