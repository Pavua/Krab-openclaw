# -*- coding: utf-8 -*-
"""Тесты Prometheus metrics endpoint и collector'а."""

from __future__ import annotations

import sys
from typing import Any

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# _format_metric / _sanitize_label
# ---------------------------------------------------------------------------


def test_format_metric_basic():
    from src.core.prometheus_metrics import _format_metric

    r = _format_metric("test_metric", 42)
    assert "# TYPE test_metric gauge" in r
    assert "test_metric 42" in r


def test_format_metric_with_help():
    from src.core.prometheus_metrics import _format_metric

    r = _format_metric("test_metric", 1, help_text="Test help")
    assert "# HELP test_metric Test help" in r
    assert "# TYPE test_metric gauge" in r


def test_format_metric_with_labels():
    from src.core.prometheus_metrics import _format_metric

    r = _format_metric("test_counter", 5, labels={"provider": "gemini"}, mtype="counter")
    assert 'test_counter{provider="gemini"} 5' in r
    assert "# TYPE test_counter counter" in r


def test_format_metric_counter_type():
    from src.core.prometheus_metrics import _format_metric

    r = _format_metric("events_total", 10, mtype="counter")
    assert "# TYPE events_total counter" in r


def test_sanitize_label_escapes_quotes():
    from src.core.prometheus_metrics import _sanitize_label

    assert _sanitize_label('say "hi"') == 'say \\"hi\\"'


def test_sanitize_label_escapes_newlines():
    from src.core.prometheus_metrics import _sanitize_label

    assert "\n" not in _sanitize_label("multi\nline")


def test_sanitize_label_escapes_backslash():
    from src.core.prometheus_metrics import _sanitize_label

    assert _sanitize_label("path\\to") == "path\\\\to"


def test_format_metric_label_with_quotes_sanitized():
    from src.core.prometheus_metrics import _format_metric

    r = _format_metric("m", 1, labels={"name": 'has "quote"'})
    # Kавычки должны быть заэскейплены
    assert 'has \\"quote\\"' in r


# ---------------------------------------------------------------------------
# collect_metrics()
# ---------------------------------------------------------------------------


def test_collect_metrics_returns_text():
    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert isinstance(text, str)
    assert len(text) > 0
    assert "krab_metrics_generated_at" in text


def test_collect_metrics_ends_with_newline():
    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert text.endswith("\n")


def test_collect_metrics_has_generated_at_type():
    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "# TYPE krab_metrics_generated_at gauge" in text


def test_collect_metrics_handles_missing_modules(monkeypatch):
    """Graceful если отсутствуют опциональные модули."""
    # Force miss: подсунем None в sys.modules до импорта внутри collect
    monkeypatch.setitem(sys.modules, "src.core.memory_validator", None)
    monkeypatch.setitem(sys.modules, "src.core.reminders_queue", None)
    monkeypatch.setitem(sys.modules, "src.core.auto_restart_policy", None)

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    # Должен всё равно отдать хотя бы generated_at без исключений
    assert "krab_metrics_generated_at" in text


def test_collect_metrics_fast(monkeypatch):
    """Ответ < 1s даже при отсутствии источников."""
    import time

    from src.core.prometheus_metrics import collect_metrics

    t0 = time.perf_counter()
    collect_metrics()
    elapsed = time.perf_counter() - t0
    assert elapsed < 1.0, f"collect_metrics слишком медленный: {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Endpoint /metrics
# ---------------------------------------------------------------------------


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"status": "ok", "provider": "google", "model": "gemini-3-pro"}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


def _make_client() -> TestClient:
    deps: dict[str, Any] = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def test_metrics_endpoint_returns_200():
    client = _make_client()
    resp = client.get("/metrics")
    assert resp.status_code == 200


def test_metrics_endpoint_content_type():
    client = _make_client()
    resp = client.get("/metrics")
    ct = resp.headers.get("content-type", "")
    assert "text/plain" in ct
    assert "0.0.4" in ct


def test_metrics_endpoint_contains_generated_at():
    client = _make_client()
    resp = client.get("/metrics")
    assert "krab_metrics_generated_at" in resp.text


def test_metrics_endpoint_returns_prometheus_format():
    client = _make_client()
    resp = client.get("/metrics")
    # Prometheus text format: # HELP / # TYPE / name value
    assert "# TYPE " in resp.text


def test_metrics_endpoint_error_fallback(monkeypatch):
    """Если collect_metrics падает — endpoint возвращает 500 с # ERROR."""
    from src.core import prometheus_metrics as pm

    def _boom():
        raise RuntimeError("forced failure")

    monkeypatch.setattr(pm, "collect_metrics", _boom)
    client = _make_client()
    resp = client.get("/metrics")
    assert resp.status_code == 500
    assert "# ERROR" in resp.text


# ---------------------------------------------------------------------------
# Новые метрики: commands / llm_latency / chat_filter / chat_windows
# ---------------------------------------------------------------------------


def test_metrics_include_command_counters():
    """command_registry.bump_command → krab_command_invocations_total в output."""
    from src.core.command_registry import _command_usage

    _command_usage.clear()
    _command_usage["help"] = 7
    _command_usage["search"] = 3

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_command_invocations_total" in text
    assert 'command="help"' in text
    assert 'command="search"' in text
    # Значения присутствуют
    assert "} 7" in text or "7\n" in text

    _command_usage.clear()


def test_metrics_command_counter_empty_when_no_usage():
    """Если usage пуст — метрика не падает, просто отсутствует."""
    from src.core.command_registry import _command_usage

    _command_usage.clear()

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    # Не должно быть ошибок — generated_at всегда есть
    assert "krab_metrics_generated_at" in text


def test_metrics_include_llm_latency_histogram():
    """llm_latency_tracker.observe → histogram в Prometheus output."""
    from src.core.llm_latency_tracker import llm_latency_tracker

    llm_latency_tracker.reset()
    llm_latency_tracker.observe(provider="google", model="gemini-3-pro", duration_s=0.8)
    llm_latency_tracker.observe(provider="google", model="gemini-3-pro", duration_s=2.0)

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_llm_route_latency_seconds_bucket" in text
    assert "krab_llm_route_latency_seconds_sum" in text
    assert "krab_llm_route_latency_seconds_count" in text
    assert 'provider="google"' in text
    assert 'model="gemini-3-pro"' in text

    llm_latency_tracker.reset()


def test_metrics_llm_latency_multiple_providers():
    """Разные провайдеры — отдельные series в histogram."""
    from src.core.llm_latency_tracker import llm_latency_tracker

    llm_latency_tracker.reset()
    llm_latency_tracker.observe(provider="google", model="gemini-3-pro", duration_s=0.5)
    llm_latency_tracker.observe(provider="openai", model="gpt-5.4", duration_s=1.5)

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert 'provider="google"' in text
    assert 'provider="openai"' in text

    llm_latency_tracker.reset()


def test_metrics_llm_latency_empty_when_no_observations():
    """Если нет наблюдений — histogram отсутствует, не падает."""
    from src.core.llm_latency_tracker import llm_latency_tracker

    llm_latency_tracker.reset()

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    # generated_at всегда есть
    assert "krab_metrics_generated_at" in text
    # Histogram может отсутствовать (нет данных)
    # — не падает — это главное


def test_metrics_include_chat_filter_modes():
    """chat_filter_config.set_mode → krab_chat_filter_modes_total в output."""
    from src.core.chat_filter_config import chat_filter_config

    # Сбросим состояние
    chat_filter_config._modes.clear()
    chat_filter_config.set_mode("chat_a", "muted")
    chat_filter_config.set_mode("chat_b", "mention-only")

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_chat_filter_modes_total" in text
    assert 'mode="muted"' in text
    assert 'mode="mention-only"' in text

    chat_filter_config._modes.clear()


def test_metrics_chat_filter_empty_when_no_overrides():
    """Если нет явных режимов — метрика не падает."""
    from src.core.chat_filter_config import chat_filter_config

    chat_filter_config._modes.clear()

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_metrics_generated_at" in text


def test_metrics_include_chat_windows():
    """chat_window_manager.get_or_create → krab_chat_windows_active в output."""
    from src.core.chat_window_manager import chat_window_manager

    chat_window_manager._windows.clear()
    chat_window_manager.get_or_create("test_cw_1")
    chat_window_manager.get_or_create("test_cw_2")
    chat_window_manager.get_or_create("test_cw_1").push({"text": "hello"})

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_chat_windows_active" in text
    assert "krab_chat_windows_capacity" in text
    assert "krab_chat_windows_total_messages" in text

    chat_window_manager._windows.clear()


def test_metrics_chat_windows_counts_messages():
    """total_messages корректно считает буферизованные сообщения."""
    from src.core.chat_window_manager import chat_window_manager

    chat_window_manager._windows.clear()
    w = chat_window_manager.get_or_create("cnt_test")
    for i in range(5):
        w.push({"id": i})

    stats = chat_window_manager.stats()
    assert stats["total_messages"] == 5
    assert stats["active_windows"] == 1

    chat_window_manager._windows.clear()


def test_metrics_chat_windows_zero_when_empty():
    """Пустой менеджер → нулевые счётчики."""
    from src.core.chat_window_manager import chat_window_manager

    chat_window_manager._windows.clear()

    from src.core.prometheus_metrics import collect_metrics

    text = collect_metrics()
    assert "krab_chat_windows_active" in text
    # Значение 0
    assert "krab_chat_windows_active 0" in text

    chat_window_manager._windows.clear()
