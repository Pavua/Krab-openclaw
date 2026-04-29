# -*- coding: utf-8 -*-
"""Тесты AnomalyDetector — sliding window z-score детектора."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.anomaly_detector import AnomalyDetector


def _clock(start: datetime) -> tuple[list[datetime], object]:
    """Хелпер: мутабельный «сейчас» + now_fn, как в chat_ban_cache тестах."""
    holder = [start]
    return holder, lambda: holder[0]


def test_baseline_accumulates_until_min_samples() -> None:
    # Пока не накопили min_samples, detect_anomalies молчит даже при
    # явных выбросах — это правильно: на 3 точках z-score шумит.
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=10)

    for i in range(5):
        holder[0] += timedelta(minutes=1)
        det.record_metric("response_length_avg", 100.0 + i)

    # Резкий выброс — но точек слишком мало.
    holder[0] += timedelta(minutes=1)
    det.record_metric("response_length_avg", 9999.0)

    assert det.detect_anomalies() == []
    assert "response_length_avg" in det.metric_names()


def test_z_score_detects_high_severity_spike() -> None:
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=10)

    # 20 стабильных точек около 100 с малым шумом.
    for i in range(20):
        holder[0] += timedelta(minutes=1)
        det.record_metric("response_time_p95", 100.0 + (i % 3) * 0.5)

    # Резкий выброс ×100 — должен дать severity=high.
    holder[0] += timedelta(minutes=1)
    det.record_metric("response_time_p95", 10000.0)

    anomalies = det.detect_anomalies()
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a.metric == "response_time_p95"
    assert a.severity == "high"
    assert a.current_value == 10000.0
    assert a.baseline_value < 200.0
    assert a.z_score > 3.0


def test_sliding_window_expires_old_samples() -> None:
    holder, now_fn = _clock(datetime(2026, 4, 28, 0, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=5, window_hours=24.0)

    # Старые точки (>24h назад) — должны протухнуть и удалиться из окна.
    for i in range(15):
        det.record_metric("error_rate", 0.01 + i * 0.001)
        holder[0] += timedelta(minutes=1)

    # Перематываем время на 25h вперёд — все предыдущие точки expired.
    holder[0] += timedelta(hours=25)

    # Триггерим prune через record.
    det.record_metric("error_rate", 0.5)
    # Окно содержит ровно 1 свежую точку (остальное удалено).
    assert det.metric_names() == ["error_rate"]
    # min_samples=5, точка одна → детектор молчит.
    assert det.detect_anomalies() == []


def test_persistence_round_trip(tmp_path: Path) -> None:
    storage = tmp_path / "anomaly_baselines.json"
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(storage_path=storage, now_fn=now_fn, min_samples=10)

    for i in range(15):
        holder[0] += timedelta(minutes=1)
        det.record_metric("tool_call_count", 5.0 + (i % 2))

    assert storage.exists()
    raw = json.loads(storage.read_text())
    assert "tool_call_count" in raw
    assert len(raw["tool_call_count"]) == 15

    # Новый detector, тот же путь — должен подхватить историю.
    det2 = AnomalyDetector(storage_path=storage, now_fn=now_fn, min_samples=10)
    assert "tool_call_count" in det2.metric_names()
    # И способен детектировать выброс на восстановленной истории.
    holder[0] += timedelta(minutes=1)
    det2.record_metric("tool_call_count", 9999.0)
    anomalies = det2.detect_anomalies()
    assert len(anomalies) == 1
    assert anomalies[0].metric == "tool_call_count"


def test_multi_metric_isolation() -> None:
    # Аномалия в одной метрике не должна влиять на другую.
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=10)

    for i in range(15):
        holder[0] += timedelta(minutes=1)
        det.record_metric("response_length_avg", 200.0 + (i % 3))
        det.record_metric("error_rate", 0.05 + (i % 2) * 0.001)

    # Выброс только в response_length_avg.
    holder[0] += timedelta(minutes=1)
    det.record_metric("response_length_avg", 50000.0)
    det.record_metric("error_rate", 0.05)

    anomalies = det.detect_anomalies()
    metrics = {a.metric for a in anomalies}
    assert "response_length_avg" in metrics
    assert "error_rate" not in metrics


def test_severity_tiers_medium_vs_high() -> None:
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=10)

    # 20 точек: mean=100, std≈1 (малый разброс).
    for i in range(20):
        holder[0] += timedelta(minutes=1)
        det.record_metric("m_medium", 100.0 + (i % 2))
        det.record_metric("m_high", 100.0 + (i % 2))

    # Medium: z≈2.5 (current=102.5, mean=100.5, std≈0.5 → z≈4? нет, шум 0/1)
    # Здесь тщательнее: с (i % 2) → значения чередуются 100.0/101.0,
    # mean=100.5, std=0.5. Тогда current=101.75 даст z=2.5 → medium.
    holder[0] += timedelta(minutes=1)
    det.record_metric("m_medium", 101.75)
    # High: z>3.0 — например, 102.5 даст z=4 → high.
    det.record_metric("m_high", 102.5)

    anomalies = {a.metric: a for a in det.detect_anomalies()}
    assert "m_medium" in anomalies
    assert "m_high" in anomalies
    assert anomalies["m_medium"].severity == "medium"
    assert anomalies["m_high"].severity == "high"


def test_constant_metric_yields_no_anomaly() -> None:
    # std==0 → защита от деления на ноль и от шумного «любое отклонение high».
    holder, now_fn = _clock(datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc))
    det = AnomalyDetector(now_fn=now_fn, min_samples=10)

    for _ in range(15):
        holder[0] += timedelta(minutes=1)
        det.record_metric("flat", 42.0)
    holder[0] += timedelta(minutes=1)
    det.record_metric("flat", 999.0)

    # Не должно быть аномалий: baseline std==0, defensive skip.
    assert det.detect_anomalies() == []


def test_invalid_values_are_dropped() -> None:
    # NaN/inf/строки не должны попадать в окно.
    det = AnomalyDetector(min_samples=5)
    det.record_metric("m", float("nan"))
    det.record_metric("m", float("inf"))
    det.record_metric("m", "not-a-number")  # type: ignore[arg-type]
    assert det.metric_names() == []
