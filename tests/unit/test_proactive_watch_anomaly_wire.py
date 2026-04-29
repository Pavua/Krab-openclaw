# -*- coding: utf-8 -*-
"""
Wire-up Idea 26: anomaly_detector ↔ proactive_watch.

Покрываем:
1) при включённом `KRAB_ANOMALY_DETECTION_ENABLED` метрики действительно
   записываются в anomaly_detector;
2) обнаруженная аномалия по нашей метрике приводит к alert и записи cooldown;
3) повторный вызов в пределах cooldown-окна не триггерит дубль-alert.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import src.core.proactive_watch as proactive_watch_module
from src.core.anomaly_detector import AnomalyDetector
from src.core.proactive_watch import ProactiveWatchService


@pytest.fixture()
def isolated_detector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AnomalyDetector:
    """Изолируем module-level singleton, чтобы не пачкать persisted state хоста."""
    detector = AnomalyDetector(
        storage_path=tmp_path / "anomaly_baselines.json",
        min_samples=5,
    )
    monkeypatch.setattr(proactive_watch_module, "anomaly_detector", detector)
    return detector


@pytest.fixture()
def service_with_isolated_cooldowns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> ProactiveWatchService:
    """ProactiveWatchService с per-test cooldown файлом."""
    cooldown_path = tmp_path / "anomaly_alert_cooldowns.json"
    monkeypatch.setattr(
        ProactiveWatchService,
        "_anomaly_cooldown_path",
        staticmethod(lambda: cooldown_path),
    )
    service = ProactiveWatchService(state_path=tmp_path / "state.json")
    return service


@pytest.mark.asyncio
async def test_anomaly_metrics_recorded_when_enabled(
    isolated_detector: AnomalyDetector,
    service_with_isolated_cooldowns: ProactiveWatchService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Включённый detector должен принять собранные метрики и сохранить их."""
    monkeypatch.setenv("KRAB_ANOMALY_DETECTION_ENABLED", "1")
    fixed_metrics = {
        "response_time_p95": 120.0,
        "error_rate": 1.5,
        "inbox_open_count": 4.0,
        "memory_indexer_queue_size": 2.0,
        "chat_filter_silence_ratio": 0.1,
    }
    monkeypatch.setattr(
        service_with_isolated_cooldowns,
        "_collect_anomaly_metrics",
        lambda: dict(fixed_metrics),
    )

    result = await service_with_isolated_cooldowns.run_anomaly_checks()

    assert result["enabled"] is True
    assert result["recorded"] == 5
    # Все 5 метрик действительно дошли до detector'а.
    assert set(isolated_detector.metric_names()) == set(fixed_metrics.keys())
    # При первом измерении aномалии быть не должно (мало точек, нет baseline).
    assert result["alerts"] == []


@pytest.mark.asyncio
async def test_anomaly_triggers_alert_with_cooldown_persisted(
    isolated_detector: AnomalyDetector,
    service_with_isolated_cooldowns: ProactiveWatchService,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Чёткий spike после стабильной истории должен быть зарегистрирован как alert."""
    monkeypatch.setenv("KRAB_ANOMALY_DETECTION_ENABLED", "1")
    metric_name = "response_time_p95"
    base_ts = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    # Засеваем стабильное окно baseline'ом (вариация есть — std != 0).
    for i, value in enumerate([100.0, 102.0, 99.0, 101.0, 100.5, 99.5, 100.0]):
        isolated_detector.record_metric(metric_name, value, ts=base_ts + timedelta(minutes=i))

    # Spike — record_metric внутри run_anomaly_checks добавит огромное значение.
    monkeypatch.setattr(
        service_with_isolated_cooldowns,
        "_collect_anomaly_metrics",
        lambda: {metric_name: 5000.0},
    )

    result = await service_with_isolated_cooldowns.run_anomaly_checks()

    assert result["enabled"] is True
    assert len(result["alerts"]) == 1
    alert = result["alerts"][0]
    assert alert["metric"] == metric_name
    assert alert["severity"] in ("high", "medium")
    # Cooldown файл создан.
    cooldown_path = ProactiveWatchService._anomaly_cooldown_path()
    assert cooldown_path.exists()
    payload = cooldown_path.read_text(encoding="utf-8")
    assert metric_name in payload


@pytest.mark.asyncio
async def test_anomaly_alert_cooldown_blocks_duplicate(
    isolated_detector: AnomalyDetector,
    service_with_isolated_cooldowns: ProactiveWatchService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """В пределах cooldown окна повторный alert по той же метрике не выдаётся."""
    monkeypatch.setenv("KRAB_ANOMALY_DETECTION_ENABLED", "1")
    metric_name = "error_rate"
    base_ts = datetime(2026, 4, 29, 10, 0, tzinfo=timezone.utc)
    for i, value in enumerate([1.0, 1.1, 0.9, 1.05, 0.95, 1.0, 1.02]):
        isolated_detector.record_metric(metric_name, value, ts=base_ts + timedelta(minutes=i))

    monkeypatch.setattr(
        service_with_isolated_cooldowns,
        "_collect_anomaly_metrics",
        lambda: {metric_name: 95.0},
    )

    first = await service_with_isolated_cooldowns.run_anomaly_checks()
    assert len(first["alerts"]) == 1, first

    # Второй прогон сразу же — cooldown должен заблокировать новый alert,
    # хотя detector всё ещё видит spike в окне.
    second = await service_with_isolated_cooldowns.run_anomaly_checks()
    assert second["alerts"] == []


@pytest.mark.asyncio
async def test_anomaly_detection_disabled_by_default(
    service_with_isolated_cooldowns: ProactiveWatchService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без env-флага run_anomaly_checks возвращает enabled=False и ничего не пишет."""
    monkeypatch.delenv("KRAB_ANOMALY_DETECTION_ENABLED", raising=False)
    called = {"collect": 0}

    def _fake_collect() -> dict[str, float]:
        called["collect"] += 1
        return {}

    monkeypatch.setattr(service_with_isolated_cooldowns, "_collect_anomaly_metrics", _fake_collect)

    result = await service_with_isolated_cooldowns.run_anomaly_checks()
    assert result == {"enabled": False, "recorded": 0, "alerts": []}
    assert called["collect"] == 0
