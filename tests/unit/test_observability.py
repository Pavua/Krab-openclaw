# -*- coding: utf-8 -*-
"""
Тесты для src/core/observability.py:
EventTimeline, MetricsRegistry, LatencyTracker, вспомогательные функции.
"""

import pytest

from src.core.observability import (
    EventTimeline,
    LatencyTracker,
    MetricsRegistry,
    build_ops_response,
    get_observability_snapshot,
    mask_secrets,
    track_event,
)

# ---------------------------------------------------------------------------
# LatencyTracker
# ---------------------------------------------------------------------------


class TestLatencyTracker:
    """Тесты кольцевого буфера задержек."""

    def test_empty_returns_zero(self):
        """Пустой трекер возвращает 0 для любого перцентиля."""
        t = LatencyTracker()
        assert t.get_percentile(0.50) == 0.0
        assert t.get_percentile(0.95) == 0.0

    def test_single_value_all_percentiles(self):
        """Один элемент — все перцентили равны этому элементу."""
        t = LatencyTracker()
        t.add(42.0)
        assert t.get_percentile(0.0) == 42.0
        assert t.get_percentile(0.5) == 42.0
        assert t.get_percentile(1.0) == 42.0

    def test_p50_p95_distinct(self):
        """p50 < p95 для распределённых данных."""
        t = LatencyTracker()
        for v in range(1, 101):  # 1..100
            t.add(float(v))
        assert t.get_percentile(0.50) < t.get_percentile(0.95)

    def test_max_size_ring_buffer(self):
        """После переполнения старые значения вытесняются."""
        t = LatencyTracker(max_size=3)
        for v in [1.0, 2.0, 3.0, 999.0]:
            t.add(v)
        # 1.0 должна быть вытеснена — p50 не равен 1.0
        p50 = t.get_percentile(0.50)
        assert p50 != 1.0


# ---------------------------------------------------------------------------
# MetricsRegistry
# ---------------------------------------------------------------------------


class TestMetricsRegistry:
    """Тесты счётчиков, gauge и агрегации метрик."""

    def test_counter_increments(self):
        """Счётчик инкрементируется корректно."""
        m = MetricsRegistry()
        m.inc("hits")
        m.inc("hits")
        m.inc("hits", value=3)
        snap = m.get_snapshot()
        assert snap["counters"]["hits"] == 5

    def test_gauge_set_and_override(self):
        """Gauge перезаписывается при повторном вызове."""
        m = MetricsRegistry()
        m.set_gauge("cpu", 0.3)
        m.set_gauge("cpu", 0.9)
        snap = m.get_snapshot()
        assert snap["gauges"]["cpu"] == pytest.approx(0.9)

    def test_snapshot_contains_latency_keys(self):
        """Снимок метрик содержит ключи p50_ms и p95_ms."""
        m = MetricsRegistry()
        m.add_latency(10.0)
        snap = m.get_snapshot()
        assert "p50_ms" in snap["latencies"]
        assert "p95_ms" in snap["latencies"]

    def test_multiple_counters_independent(self):
        """Разные счётчики не влияют друг на друга."""
        m = MetricsRegistry()
        m.inc("a", 5)
        m.inc("b", 2)
        snap = m.get_snapshot()
        assert snap["counters"]["a"] == 5
        assert snap["counters"]["b"] == 2


# ---------------------------------------------------------------------------
# EventTimeline
# ---------------------------------------------------------------------------


class TestEventTimeline:
    """Тесты записи событий, фильтрации и усечения тайmlайна."""

    def test_append_and_retrieve(self):
        """Записанное событие присутствует в get_events."""
        tl = EventTimeline()
        tl.append("test.event", severity="info")
        events = tl.get_events()
        names = [e["name"] for e in events]
        assert "test.event" in names

    def test_event_fields_present(self):
        """Каждое событие содержит обязательные поля."""
        tl = EventTimeline()
        tl.append("my.event", severity="warn", details={"k": "v"}, channel="bot")
        ev = tl.get_events(limit=1)[0]
        assert ev["name"] == "my.event"
        assert ev["severity"] == "warn"
        assert ev["channel"] == "bot"
        assert ev["details"] == {"k": "v"}
        assert "ts" in ev
        assert "time_iso" in ev

    def test_severity_filtering(self):
        """min_severity фильтрует события ниже порога."""
        tl = EventTimeline()
        tl.append("low", severity="info")
        tl.append("mid", severity="warn")
        tl.append("high", severity="error")
        events = tl.get_events(min_severity="warn")
        names = {e["name"] for e in events}
        assert "low" not in names
        assert "mid" in names
        assert "high" in names

    def test_channel_filtering(self):
        """Фильтрация по каналу возвращает только нужные события."""
        tl = EventTimeline()
        tl.append("ev1", channel="telegram")
        tl.append("ev2", channel="swarm")
        telegram_events = tl.get_events(channel="telegram")
        assert all(e["channel"] == "telegram" for e in telegram_events)

    def test_timeline_truncation_max_size(self):
        """Кольцевой буфер не превышает max_size."""
        tl = EventTimeline(max_size=5)
        for i in range(10):
            tl.append(f"ev_{i}")
        # limit по умолчанию 200, но буфер ограничен 5
        events = tl.get_events(limit=200)
        assert len(events) <= 5

    def test_limit_parameter(self):
        """Параметр limit ограничивает количество возвращаемых событий."""
        tl = EventTimeline()
        for i in range(20):
            tl.append(f"ev_{i}")
        events = tl.get_events(limit=5)
        assert len(events) == 5

    def test_critical_severity_passes_all_filters(self):
        """critical-события проходят через любой min_severity фильтр."""
        tl = EventTimeline()
        tl.append("crit.event", severity="critical")
        for level in ("info", "warn", "error", "critical"):
            events = tl.get_events(min_severity=level)
            names = [e["name"] for e in events]
            assert "crit.event" in names


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


class TestMaskSecrets:
    """Тесты маскировки чувствительных данных."""

    def test_masks_api_key(self):
        """api_key заменяется на MASKED."""
        result = mask_secrets({"api_key": "secret123", "model": "gemini"})
        assert result["api_key"] == "***MASKED***"
        assert result["model"] == "gemini"

    def test_masks_nested(self):
        """Маскировка работает рекурсивно во вложенных структурах."""
        payload = {"config": {"password": "hunter2", "host": "localhost"}}
        result = mask_secrets(payload)
        assert result["config"]["password"] == "***MASKED***"
        assert result["config"]["host"] == "localhost"

    def test_masks_in_list(self):
        """Маскировка проходит по элементам списка."""
        payload = [{"token": "abc"}, {"name": "krab"}]
        result = mask_secrets(payload)
        assert result[0]["token"] == "***MASKED***"
        assert result[1]["name"] == "krab"

    def test_passthrough_plain_value(self):
        """Скалярное значение без секретов возвращается без изменений."""
        assert mask_secrets("hello") == "hello"
        assert mask_secrets(42) == 42


class TestBuildOpsResponse:
    """Тесты стандартного ops-ответа."""

    def test_ok_response(self):
        """ok-ответ содержит корректные поля."""
        resp = build_ops_response("ok", summary="all good", data={"nodes": 3})
        assert resp["status"] == "ok"
        assert resp["summary"] == "all good"
        assert resp["data"]["nodes"] == 3
        assert resp["error_code"] == ""

    def test_degraded_response(self):
        """degraded-статус и error_code передаются корректно."""
        resp = build_ops_response("degraded", error_code="DB_TIMEOUT", summary="db slow")
        assert resp["status"] == "degraded"
        assert resp["error_code"] == "DB_TIMEOUT"

    def test_default_empty_data(self):
        """data по умолчанию — пустой словарь."""
        resp = build_ops_response("ok")
        assert resp["data"] == {}


class TestGetObservabilitySnapshot:
    """Тесты сводного снимка observability."""

    def test_snapshot_structure(self):
        """Снимок содержит ключи metrics и timeline_tail."""
        snap = get_observability_snapshot()
        assert "metrics" in snap
        assert "timeline_tail" in snap

    def test_snapshot_timeline_tail_limited(self):
        """timeline_tail не превышает 10 событий."""
        # наполняем глобальный timeline
        for i in range(20):
            track_event(f"snap_test_{i}")
        snap = get_observability_snapshot()
        assert len(snap["timeline_tail"]) <= 10
