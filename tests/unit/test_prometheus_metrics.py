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
