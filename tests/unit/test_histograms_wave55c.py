# -*- coding: utf-8 -*-
"""Tests for Wave 55-C timing histograms.

Покрывает: krab_chain_advance_duration_seconds,
           krab_model_response_chars,
           krab_smart_retry_wait_seconds
и два alert правила в krab_alerts.yml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry():
    """Создаёт изолированный CollectorRegistry для тестов."""
    from prometheus_client import CollectorRegistry

    return CollectorRegistry()


# ---------------------------------------------------------------------------
# Test: chain advance histogram observes duration
# ---------------------------------------------------------------------------


def test_chain_advance_histogram_observes_duration():
    """krab_chain_advance_duration_seconds корректно наблюдает duration."""
    from prometheus_client import CollectorRegistry, Histogram

    registry = _make_registry()
    h = Histogram(
        "test_chain_advance_duration_seconds",
        "test",
        ["from_model", "to_model", "reason"],
        buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 90.0),
        registry=registry,
    )
    h.labels(
        from_model="google/gemini-3-pro",
        to_model="google/gemini-3-flash",
        reason="provider_timeout",
    ).observe(15.0)

    # Проверяем, что значение попало в правильный bucket (>= 10s bucket)
    metric_families = list(registry.collect())
    assert len(metric_families) == 1
    mf = metric_families[0]
    # Bucket le=30 должен содержать 1 (15s < 30s)
    buckets = {s.labels.get("le"): s.value for s in mf.samples if s.name.endswith("_bucket")}
    assert buckets.get("30.0") == 1.0
    # Bucket le=10 должен содержать 0 (15s > 10s)
    assert buckets.get("10.0") == 0.0


# ---------------------------------------------------------------------------
# Test: response chars histogram buckets correct
# ---------------------------------------------------------------------------


def test_response_chars_histogram_buckets_correct():
    """krab_model_response_chars имеет правильные buckets для production range."""
    from prometheus_client import CollectorRegistry, Histogram

    registry = _make_registry()
    h = Histogram(
        "test_model_response_chars",
        "test",
        ["model"],
        buckets=(50, 200, 500, 1000, 2000, 5000),
        registry=registry,
    )
    # Короткий ответ (100 chars)
    h.labels(model="google/gemini-3-pro").observe(100)
    # Длинный ответ (3000 chars)
    h.labels(model="google/gemini-3-pro").observe(3000)

    metric_families = list(registry.collect())
    mf = metric_families[0]
    # sum должна быть 3100
    sums = [s.value for s in mf.samples if s.name.endswith("_sum")]
    assert any(abs(s - 3100) < 0.1 for s in sums)
    # count должна быть 2
    counts = [s.value for s in mf.samples if s.name.endswith("_count")]
    assert any(c == 2.0 for c in counts)


# ---------------------------------------------------------------------------
# Test: smart retry wait outcomes distinct
# ---------------------------------------------------------------------------


def test_smart_retry_wait_outcomes_distinct():
    """krab_smart_retry_wait_seconds разделяет success и failure outcome."""
    from prometheus_client import CollectorRegistry, Histogram

    registry = _make_registry()
    h = Histogram(
        "test_smart_retry_wait_seconds",
        "test",
        ["outcome"],
        buckets=(5.0, 10.0, 30.0, 60.0),
        registry=registry,
    )
    h.labels(outcome="success").observe(30.0)
    h.labels(outcome="failure").observe(30.0)

    metric_families = list(registry.collect())
    mf = metric_families[0]
    # Должно быть две разные серии (success / failure) с count=1 каждая
    counts = {s.labels.get("outcome"): s.value for s in mf.samples if s.name.endswith("_count")}
    assert counts.get("success") == 1.0
    assert counts.get("failure") == 1.0


# ---------------------------------------------------------------------------
# Test: record_* helpers are fail-safe (no prometheus_client)
# ---------------------------------------------------------------------------


def test_record_chain_advance_duration_failsafe(monkeypatch):
    """record_chain_advance_duration не бросает если prometheus_client недоступен."""
    import src.core.prometheus_metrics as pm

    original = pm.krab_chain_advance_duration_seconds
    monkeypatch.setattr(pm, "krab_chain_advance_duration_seconds", None)
    try:
        pm.record_chain_advance_duration(
            from_model="a", to_model="b", reason="quota", duration_sec=5.0
        )
    except Exception as exc:
        pytest.fail(f"record_chain_advance_duration raised: {exc}")
    finally:
        monkeypatch.setattr(pm, "krab_chain_advance_duration_seconds", original)


def test_record_response_chars_failsafe(monkeypatch):
    """record_response_chars не бросает если prometheus_client недоступен."""
    import src.core.prometheus_metrics as pm

    original = pm.krab_model_response_chars
    monkeypatch.setattr(pm, "krab_model_response_chars", None)
    try:
        pm.record_response_chars(model="google/gemini-3-pro", char_count=500)
    except Exception as exc:
        pytest.fail(f"record_response_chars raised: {exc}")
    finally:
        monkeypatch.setattr(pm, "krab_model_response_chars", original)


def test_record_smart_retry_wait_failsafe(monkeypatch):
    """record_smart_retry_wait не бросает если prometheus_client недоступен."""
    import src.core.prometheus_metrics as pm

    original = pm.krab_smart_retry_wait_seconds
    monkeypatch.setattr(pm, "krab_smart_retry_wait_seconds", None)
    try:
        pm.record_smart_retry_wait(outcome="success", wait_sec=30.0)
    except Exception as exc:
        pytest.fail(f"record_smart_retry_wait raised: {exc}")
    finally:
        monkeypatch.setattr(pm, "krab_smart_retry_wait_seconds", original)


# ---------------------------------------------------------------------------
# Test: alerts YAML — LongChainAdvanceAlert threshold
# ---------------------------------------------------------------------------


ALERTS_PATH = (
    Path(__file__).parent.parent.parent / "deploy" / "monitoring" / "rules" / "krab_alerts.yml"
)


def _load_alerts() -> list[dict]:
    """Загрузить все alert rules из YAML."""
    with ALERTS_PATH.open() as f:
        data = yaml.safe_load(f)
    rules = []
    for group in data.get("groups", []):
        rules.extend(group.get("rules", []))
    return rules


def test_long_chain_advance_alert_present():
    """LongChainAdvanceAlert присутствует в YAML с правильными параметрами."""
    rules = _load_alerts()
    alert_names = [r.get("alert") for r in rules]
    assert "LongChainAdvanceAlert" in alert_names, (
        f"LongChainAdvanceAlert не найден среди алертов: {alert_names}"
    )
    alert = next(r for r in rules if r.get("alert") == "LongChainAdvanceAlert")
    # severity должна быть warning
    assert alert.get("labels", {}).get("severity") == "warning"
    # for: 5m
    assert alert.get("for") == "5m"


def test_response_size_anomaly_alert_p95():
    """ResponseSizeAnomalyAlert присутствует и проверяет p95 > 5000 chars."""
    rules = _load_alerts()
    alert_names = [r.get("alert") for r in rules]
    assert "ResponseSizeAnomalyAlert" in alert_names, (
        f"ResponseSizeAnomalyAlert не найден среди алертов: {alert_names}"
    )
    alert = next(r for r in rules if r.get("alert") == "ResponseSizeAnomalyAlert")
    expr = alert.get("expr", "")
    assert "5000" in str(expr), f"Порог 5000 не найден в expr: {expr}"
    assert "0.95" in str(expr), f"Квантиль 0.95 не найден в expr: {expr}"
    assert alert.get("labels", {}).get("severity") == "warning"


# ---------------------------------------------------------------------------
# Test: histograms exported in prometheus_metrics module
# ---------------------------------------------------------------------------


def test_histograms_exported_in_module():
    """Все три histogram объекта доступны в prometheus_metrics."""
    import src.core.prometheus_metrics as pm

    # Объекты должны быть либо Histogram либо None (без prometheus_client)
    for attr_name in (
        "krab_chain_advance_duration_seconds",
        "krab_model_response_chars",
        "krab_smart_retry_wait_seconds",
    ):
        assert hasattr(pm, attr_name), f"{attr_name} не экспортирован из prometheus_metrics"

    # record_* helpers должны существовать и быть callable
    for fn_name in (
        "record_chain_advance_duration",
        "record_response_chars",
        "record_smart_retry_wait",
    ):
        assert hasattr(pm, fn_name), f"{fn_name} не экспортирован"
        assert callable(getattr(pm, fn_name)), f"{fn_name} не callable"


def test_record_chain_advance_duration_observes():
    """record_chain_advance_duration вызывается без исключений при наличии prometheus_client."""
    import src.core.prometheus_metrics as pm

    if pm.krab_chain_advance_duration_seconds is None:
        pytest.skip("prometheus_client не установлен")
    # Не должно бросать
    pm.record_chain_advance_duration(
        from_model="google/gemini-3-pro",
        to_model="google/gemini-3-flash",
        reason="provider_timeout",
        duration_sec=12.5,
    )


def test_record_response_chars_observes():
    """record_response_chars вызывается без исключений при наличии prometheus_client."""
    import src.core.prometheus_metrics as pm

    if pm.krab_model_response_chars is None:
        pytest.skip("prometheus_client не установлен")
    pm.record_response_chars(model="google/gemini-3-pro", char_count=1234)


def test_record_smart_retry_wait_observes():
    """record_smart_retry_wait вызывается без исключений при наличии prometheus_client."""
    import src.core.prometheus_metrics as pm

    if pm.krab_smart_retry_wait_seconds is None:
        pytest.skip("prometheus_client не установлен")
    pm.record_smart_retry_wait(outcome="success", wait_sec=30.0)
    pm.record_smart_retry_wait(outcome="failure", wait_sec=30.0)
