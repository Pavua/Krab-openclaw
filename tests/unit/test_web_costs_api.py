# -*- coding: utf-8 -*-
"""
Тесты FinOps / Costs API эндпоинтов web-панели.

Покрываем:
  GET /api/costs/report   — отчёт по расходам
  GET /api/costs/budget   — состояние бюджета
  GET /api/costs/history  — история вызовов модели
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ── Заглушки ──────────────────────────────────────────────────────────────────


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def get_model_info(self):
        return {}


def _make_client() -> TestClient:
    """Создаёт TestClient с минимальным набором зависимостей."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": None,
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


# ── /api/costs/report ─────────────────────────────────────────────────────────


def test_costs_report_returns_ok():
    """Базовый ответ: ok=True и вложенный dict report."""
    client = _make_client()
    resp = client.get("/api/costs/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "report" in data


def test_costs_report_has_required_fields():
    """Отчёт содержит все обязательные FinOps поля."""
    client = _make_client()
    report = client.get("/api/costs/report").json()["report"]
    required = {
        "total_cost_usd",
        "total_calls",
        "budget_monthly_usd",
        "budget_remaining_usd",
        "budget_used_pct",
        "by_model",
        "period_start",
        "period_end",
        "input_tokens",
        "output_tokens",
    }
    assert required <= set(report.keys())


def test_costs_report_numeric_values():
    """Числовые поля отчёта — неотрицательные числа."""
    client = _make_client()
    report = client.get("/api/costs/report").json()["report"]
    assert isinstance(report["total_cost_usd"], (int, float))
    assert report["total_cost_usd"] >= 0
    assert isinstance(report["total_calls"], int)
    assert report["total_calls"] >= 0
    assert report["budget_used_pct"] >= 0


def test_costs_report_reflects_recorded_calls():
    """После добавления вызовов в cost_analytics, отчёт отражает их стоимость."""
    from src.core.cost_analytics import CostAnalytics

    # Создаём изолированный экземпляр аналитики с бюджетом 100 USD
    ca = CostAnalytics(monthly_budget_usd=100.0)
    ca.record_usage(
        {"prompt_tokens": 1000, "completion_tokens": 500},
        model_id="google/gemini-3-pro-preview",
    )

    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        report = client.get("/api/costs/report").json()["report"]

    # Хотя бы один вызов должен отражаться
    assert report["total_calls"] >= 1
    # Бюджет задан, значит remaining < budget
    assert report["budget_remaining_usd"] < report["budget_monthly_usd"]


# ── /api/costs/budget ─────────────────────────────────────────────────────────


def test_costs_budget_returns_ok():
    """Базовый ответ: ok=True и вложенный dict budget."""
    client = _make_client()
    resp = client.get("/api/costs/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "budget" in data


def test_costs_budget_fields_present():
    """Все обязательные поля бюджета присутствуют."""
    client = _make_client()
    budget = client.get("/api/costs/budget").json()["budget"]
    assert "monthly_limit_usd" in budget
    assert "spent_usd" in budget
    assert "remaining_usd" in budget
    assert "budget_ok" in budget
    assert "used_pct" in budget
    assert "forecast_calls" in budget


def test_costs_budget_ok_is_true_when_no_budget():
    """Без заданного бюджета budget_ok всегда True."""
    from src.core.cost_analytics import CostAnalytics

    ca = CostAnalytics(monthly_budget_usd=0.0)
    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        budget = client.get("/api/costs/budget").json()["budget"]
    assert budget["budget_ok"] is True
    assert budget["monthly_limit_usd"] is None


def test_costs_budget_detects_overrun():
    """Если потрачено больше лимита, budget_ok=False."""
    from src.core.cost_analytics import CostAnalytics

    ca = CostAnalytics(monthly_budget_usd=0.001)  # крошечный лимит
    # Записываем дорогой вызов
    ca.record_usage(
        {"prompt_tokens": 100_000, "completion_tokens": 50_000},
        model_id="google/gemini-3-pro-preview",
    )
    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        budget = client.get("/api/costs/budget").json()["budget"]
    assert budget["budget_ok"] is False
    assert budget["spent_usd"] > 0


# ── /api/costs/history ────────────────────────────────────────────────────────


def test_costs_history_returns_ok():
    """Базовый ответ: ok=True, history — список."""
    client = _make_client()
    resp = client.get("/api/costs/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["history"], list)


def test_costs_history_limit_param():
    """Параметр limit ограничивает число возвращаемых записей."""
    from src.core.cost_analytics import CostAnalytics

    ca = CostAnalytics()
    for i in range(10):
        ca.record_usage(
            {"prompt_tokens": 100 + i, "completion_tokens": 50},
            model_id="google/gemini-3-flash-preview",
            channel="telegram",
        )
    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        data = client.get("/api/costs/history?limit=3").json()
    assert data["returned"] == 3
    assert len(data["history"]) == 3


def test_costs_history_channel_filter():
    """Фильтр по channel возвращает только записи нужного канала."""
    from src.core.cost_analytics import CostAnalytics

    ca = CostAnalytics()
    ca.record_usage({"prompt_tokens": 100, "completion_tokens": 50}, channel="telegram")
    ca.record_usage({"prompt_tokens": 200, "completion_tokens": 80}, channel="web")
    ca.record_usage({"prompt_tokens": 150, "completion_tokens": 60}, channel="telegram")

    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        data = client.get("/api/costs/history?channel=telegram").json()

    # Все возвращённые записи принадлежат каналу telegram
    assert all(r["channel"] == "telegram" for r in data["history"])
    assert data["returned"] == 2


def test_costs_history_record_fields():
    """Каждая запись истории содержит нужные поля."""
    from src.core.cost_analytics import CostAnalytics

    ca = CostAnalytics()
    ca.record_usage(
        {"prompt_tokens": 500, "completion_tokens": 200},
        model_id="google/gemini-3-pro-preview",
        channel="telegram",
        is_fallback=False,
        tool_calls_count=2,
    )
    with patch("src.core.cost_analytics.cost_analytics", ca):
        client = _make_client()
        record = client.get("/api/costs/history?limit=1").json()["history"][0]

    assert record["model_id"] == "google/gemini-3-pro-preview"
    assert record["input_tokens"] == 500
    assert record["output_tokens"] == 200
    assert "cost_usd" in record
    assert "timestamp" in record
    assert record["channel"] == "telegram"
    assert record["is_fallback"] is False
    assert record["tool_calls_count"] == 2
