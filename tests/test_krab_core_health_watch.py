# -*- coding: utf-8 -*-
"""Тесты аналитики health-watch для Krab Core."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "krab_core_health_watch.py"
    spec = importlib.util.spec_from_file_location("krab_core_health_watch", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_analyze_all_up() -> None:
    m = _load_module()
    samples = [
        m.HealthSample(ts_utc="2026-01-01T00:00:00+00:00", http_up=True, http_status=200, error="", core_pids=[1]),
        m.HealthSample(ts_utc="2026-01-01T00:00:02+00:00", http_up=True, http_status=200, error="", core_pids=[1]),
    ]
    res = m._analyze(samples)
    assert res["ok"] is True
    assert res["down_count"] == 0
    assert res["flaps"] == 0


def test_analyze_detects_down_and_flaps() -> None:
    m = _load_module()
    samples = [
        m.HealthSample(ts_utc="2026-01-01T00:00:00+00:00", http_up=True, http_status=200, error="", core_pids=[1]),
        m.HealthSample(ts_utc="2026-01-01T00:00:02+00:00", http_up=False, http_status=0, error="timeout", core_pids=[]),
        m.HealthSample(ts_utc="2026-01-01T00:00:04+00:00", http_up=True, http_status=200, error="", core_pids=[1]),
    ]
    res = m._analyze(samples)
    assert res["ok"] is False
    assert res["down_count"] == 1
    assert res["flaps"] == 2
    assert res["first_down_at"] == "2026-01-01T00:00:02+00:00"
