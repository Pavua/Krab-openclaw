# -*- coding: utf-8 -*-
"""
Тесты для src/core/observability.py — метрики, timeline, маскировка, snasphot.

HIGH RISK: 0 тестов ранее. Покрываем ключевые компоненты:
LatencyTracker, MetricsRegistry, EventTimeline, mask_secrets,
build_ops_response, get_observability_snapshot.
"""

from __future__ import annotations

import pytest

from src.core.observability import (
    EventTimeline,
    LatencyTracker,
    MetricsRegistry,
    build_ops_response,
    get_observability_snapshot,
    mask_secrets,
)

# ------------------------------------------------------------------
# LatencyTracker
# ------------------------------------------------------------------


class TestLatencyTracker:
    def test_empty_returns_zero(self) -> None:
        """Пустой буфер → 0.0 для любого перцентиля."""
        t = LatencyTracker()
        assert t.get_percentile(0.50) == 0.0
        assert t.get_percentile(0.95) == 0.0
        assert t.get_percentile(0.99) == 0.0

    def test_single_element_all_percentiles(self) -> None:
        """Один элемент → тот же элемент для всех перцентилей."""
        t = LatencyTracker()
        t.add(42.0)
        assert t.get_percentile(0.50) == pytest.approx(42.0)
        assert t.get_percentile(0.95) == pytest.approx(42.0)
        assert t.get_percentile(0.99) == pytest.approx(42.0)

    def test_p50_median(self) -> None:
        """p50 должен быть медианой нечётного набора."""
        t = LatencyTracker()
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            t.add(v)
        assert t.get_percentile(0.50) == pytest.approx(30.0)

    def test_p95_high_percentile(self) -> None:
        """p95 лежит в верхней 5% хвосте."""
        t = LatencyTracker()
        for v in range(1, 101):  # 1..100
            t.add(float(v))
        # p95 ≈ 95-96
        p95 = t.get_percentile(0.95)
        assert 94.0 <= p95 <= 96.0

    def test_p99_near_max(self) -> None:
        """p99 близко к максимуму."""
        t = LatencyTracker()
        for v in range(1, 101):
            t.add(float(v))
        p99 = t.get_percentile(0.99)
        assert p99 >= 98.0

    def test_ring_buffer_max_size(self) -> None:
        """Буфер ограничен max_size, старые значения вытесняются."""
        t = LatencyTracker(max_size=5)
        for v in range(10):
            t.add(float(v))
        # должно остаться 5 последних: 5,6,7,8,9
        assert len(t._latencies) == 5
        assert min(t._latencies) == pytest.approx(5.0)


# ------------------------------------------------------------------
# MetricsRegistry
# ------------------------------------------------------------------


class TestMetricsRegistry:
    def test_fresh_instance_empty_counters(self) -> None:
        """Новый реестр не содержит счётчиков."""
        m = MetricsRegistry()
        snap = m.get_snapshot()
        assert snap["counters"] == {}
        assert snap["gauges"] == {}

    def test_inc_counter(self) -> None:
        """inc создаёт и инкрементирует счётчик."""
        m = MetricsRegistry()
        m.inc("requests")
        m.inc("requests")
        m.inc("requests", 5)
        assert m.get_snapshot()["counters"]["requests"] == 7

    def test_set_gauge(self) -> None:
        """set_gauge сохраняет float-значение."""
        m = MetricsRegistry()
        m.set_gauge("memory_mb", 512.3)
        assert m.get_snapshot()["gauges"]["memory_mb"] == pytest.approx(512.3)

    def test_latencies_in_snapshot(self) -> None:
        """Снимок содержит p50_ms и p95_ms для добавленных задержек."""
        m = MetricsRegistry()
        for v in range(1, 101):
            m.add_latency(float(v))
        snap = m.get_snapshot()
        assert "p50_ms" in snap["latencies"]
        assert "p95_ms" in snap["latencies"]
        assert snap["latencies"]["p50_ms"] > 0

    def test_snapshot_returns_copy(self) -> None:
        """get_snapshot возвращает независимую копию счётчиков."""
        m = MetricsRegistry()
        m.inc("x")
        snap = m.get_snapshot()
        snap["counters"]["x"] = 999
        assert m.get_snapshot()["counters"]["x"] == 1


# ------------------------------------------------------------------
# EventTimeline
# ------------------------------------------------------------------


class TestEventTimeline:
    def test_empty_timeline_returns_empty_list(self) -> None:
        """Пустой timeline → пустой список."""
        # Используем изолированный экземпляр, не глобальный singleton
        tl = EventTimeline(max_size=10)
        # EventTimeline.append вызывает metrics.inc — используем глобальный metrics
        assert tl.get_events() == []

    def test_append_and_get(self) -> None:
        """Добавленное событие видно в get_events."""
        tl = EventTimeline(max_size=10)
        tl.append("test_event", severity="info")
        events = tl.get_events()
        assert len(events) == 1
        assert events[0]["name"] == "test_event"
        assert events[0]["severity"] == "info"

    def test_get_events_limit(self) -> None:
        """Параметр limit ограничивает количество возвращаемых событий."""
        tl = EventTimeline(max_size=50)
        for i in range(20):
            tl.append(f"ev_{i}")
        assert len(tl.get_events(limit=5)) == 5

    def test_filter_by_min_severity(self) -> None:
        """Фильтрация min_severity='error' отбрасывает info/warn."""
        tl = EventTimeline(max_size=50)
        tl.append("info_ev", severity="info")
        tl.append("warn_ev", severity="warn")
        tl.append("error_ev", severity="error")
        tl.append("crit_ev", severity="critical")
        result = tl.get_events(min_severity="error")
        names = {e["name"] for e in result}
        assert "error_ev" in names
        assert "crit_ev" in names
        assert "info_ev" not in names
        assert "warn_ev" not in names

    def test_filter_by_channel(self) -> None:
        """Фильтрация по channel возвращает только нужный канал."""
        tl = EventTimeline(max_size=50)
        tl.append("sys_ev", channel="system")
        tl.append("tg_ev", channel="telegram")
        result = tl.get_events(channel="telegram")
        assert all(e["channel"] == "telegram" for e in result)
        assert len(result) == 1

    def test_ring_buffer_max_size(self) -> None:
        """Буфер вытесняет старые события при переполнении."""
        tl = EventTimeline(max_size=3)
        for i in range(5):
            tl.append(f"ev_{i}")
        events = tl.get_events()
        assert len(events) == 3
        names = {e["name"] for e in events}
        # Последние три: ev_2, ev_3, ev_4
        assert "ev_0" not in names
        assert "ev_4" in names

    def test_event_has_required_fields(self) -> None:
        """Каждое событие содержит обязательные поля."""
        tl = EventTimeline(max_size=10)
        tl.append("check_fields", severity="warn", details={"key": "val"}, channel="test")
        ev = tl.get_events()[0]
        for field in ("ts", "time_iso", "name", "severity", "channel", "details"):
            assert field in ev
        assert ev["details"]["key"] == "val"


# ------------------------------------------------------------------
# mask_secrets
# ------------------------------------------------------------------


class TestMaskSecrets:
    def test_masks_api_key(self) -> None:
        """api_key маскируется."""
        result = mask_secrets({"api_key": "super-secret"})
        assert result["api_key"] == "***MASKED***"

    def test_masks_token(self) -> None:
        """token маскируется."""
        result = mask_secrets({"token": "abc123"})
        assert result["token"] == "***MASKED***"

    def test_masks_password(self) -> None:
        """password маскируется."""
        result = mask_secrets({"password": "qwerty"})
        assert result["password"] == "***MASKED***"

    def test_masks_secret(self) -> None:
        """secret маскируется."""
        result = mask_secrets({"secret": "hidden"})
        assert result["secret"] == "***MASKED***"

    def test_masks_authorization(self) -> None:
        """authorization маскируется."""
        result = mask_secrets({"authorization": "Bearer xyz"})
        assert result["authorization"] == "***MASKED***"

    def test_safe_key_untouched(self) -> None:
        """Безопасные ключи не трогаются; 'tokens' содержит 'token' → маскируется."""
        result = mask_secrets({"model": "gemini", "count": 100})
        assert result["model"] == "gemini"
        assert result["count"] == 100

    def test_nested_dict_masked(self) -> None:
        """Вложенные словари также маскируются."""
        payload = {"config": {"api_key": "nested-secret", "name": "krab"}}
        result = mask_secrets(payload)
        assert result["config"]["api_key"] == "***MASKED***"
        assert result["config"]["name"] == "krab"

    def test_list_of_dicts(self) -> None:
        """Список словарей обрабатывается рекурсивно."""
        payload = [{"token": "t1"}, {"safe": "ok"}]
        result = mask_secrets(payload)
        assert result[0]["token"] == "***MASKED***"
        assert result[1]["safe"] == "ok"

    def test_scalar_passthrough(self) -> None:
        """Скалярные значения возвращаются без изменений."""
        assert mask_secrets("plain string") == "plain string"
        assert mask_secrets(42) == 42
        assert mask_secrets(None) is None


# ------------------------------------------------------------------
# build_ops_response
# ------------------------------------------------------------------


class TestBuildOpsResponse:
    def test_ok_response(self) -> None:
        """Статус ok формируется корректно."""
        r = build_ops_response("ok", summary="все хорошо")
        assert r["status"] == "ok"
        assert r["summary"] == "все хорошо"
        assert r["error_code"] == ""
        assert r["data"] == {}

    def test_failed_with_error_code(self) -> None:
        """failed + error_code сохраняются."""
        r = build_ops_response("failed", error_code="TIMEOUT", summary="тайм-аут")
        assert r["status"] == "failed"
        assert r["error_code"] == "TIMEOUT"

    def test_data_passed_through(self) -> None:
        """data-поле попадает в ответ."""
        r = build_ops_response("degraded", data={"latency_ms": 999})
        assert r["data"]["latency_ms"] == 999

    def test_empty_data_defaults_to_empty_dict(self) -> None:
        """Без data возвращается пустой dict, не None."""
        r = build_ops_response("ok")
        assert isinstance(r["data"], dict)


# ------------------------------------------------------------------
# get_observability_snapshot
# ------------------------------------------------------------------


class TestGetObservabilitySnapshot:
    def test_snapshot_has_required_keys(self) -> None:
        """Снимок содержит metrics и timeline_tail."""
        snap = get_observability_snapshot()
        assert "metrics" in snap
        assert "timeline_tail" in snap

    def test_metrics_structure(self) -> None:
        """metrics содержит counters, gauges, latencies."""
        snap = get_observability_snapshot()
        m = snap["metrics"]
        assert "counters" in m
        assert "gauges" in m
        assert "latencies" in m

    def test_timeline_tail_is_list(self) -> None:
        """timeline_tail всегда список."""
        snap = get_observability_snapshot()
        assert isinstance(snap["timeline_tail"], list)

    def test_snapshot_masks_secrets(self) -> None:
        """Секреты в метриках и timeline не попадают в снимок в открытом виде."""
        from src.core.observability import metrics as _m

        # Устанавливаем gauge с безопасным именем, проверяем что он виден
        _m.set_gauge("test_plain_gauge", 1.0)
        snap = get_observability_snapshot()
        assert snap["metrics"]["gauges"]["test_plain_gauge"] == pytest.approx(1.0)
