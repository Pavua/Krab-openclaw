"""
Проверки WebRouterCompat: модуль должен пробрасывать фактический runtime-маршрут
из OpenClawClient в last_route для web-панели.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import config
from src.modules import web_router_compat as mod
from src.modules.web_router_compat import WebRouterCompat


class _FakeModelManager:
    """Минимальный стаб ModelManager для тестов WebRouterCompat."""

    def __init__(self, *, cost_analytics=None) -> None:
        self._current_model = "nvidia/nemotron-3-nano"
        self._models_cache = {}
        self.cost_analytics = cost_analytics

    def get_ram_usage(self):
        return {"available_gb": 8.0}

    def get_current_model(self):
        return self._current_model

    def is_local_model(self, model_id: str) -> bool:
        return not str(model_id).startswith(("google/", "openai/"))


class _FakeOpenClawClient:
    """Стаб OpenClawClient с управляемыми chunk-ответом и route meta."""

    def __init__(self) -> None:
        self.active_tier = "free"
        self.last_call: dict[str, object] = {}
        self._meta = {
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
            "error_code": None,
            "route_reason": "local_direct_primary",
            "route_detail": "Ответ получен напрямую из LM Studio",
            "active_tier": "free",
            "force_cloud": False,
            "timestamp": 1234567890,
        }

    async def send_message_stream(
        self,
        message: str,
        chat_id: str,
        force_cloud: bool = False,
        preferred_model: str | None = None,
    ):
        assert message
        assert chat_id == "web_assistant"
        self.last_call = {
            "message": message,
            "chat_id": chat_id,
            "force_cloud": force_cloud,
            "preferred_model": preferred_model,
        }
        yield "Локальный "
        yield "ответ"

    def get_last_runtime_route(self):
        return dict(self._meta)

    def get_usage_stats(self):
        return {"input_tokens": 111, "output_tokens": 222, "total_tokens": 333}


class _FakeCostAnalytics:
    """Минимальная аналитика затрат для проверки ops-compat контрактов."""

    def build_usage_report_dict(self):
        return {
            "input_tokens": 1200,
            "output_tokens": 800,
            "total_tokens": 2000,
            "cost_session_usd": 1.2,
            "cost_month_usd": 9.0,
            "monthly_budget_usd": 25.0,
            "remaining_budget_usd": 16.0,
            "budget_ok": True,
            "monthly_calls_forecast": 300,
            "by_model": {
                "google/gemini-3-flash-preview": {
                    "input_tokens": 1200,
                    "output_tokens": 800,
                    "cost_usd": 9.0,
                    "calls": 30,
                }
            },
        }


@pytest.mark.asyncio
async def test_route_query_exposes_runtime_route_meta():
    """
    После route_query() router.get_last_route() должен содержать runtime-маршрут,
    а не пустой словарь.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())
    reply = await router.route_query("проверка")

    assert reply == "Локальный ответ"
    last_route = router.get_last_route()
    assert last_route["channel"] == "local_direct"
    assert last_route["provider"] == "nvidia"
    assert last_route["model"] == "nvidia/nemotron-3-nano"
    assert last_route["status"] == "ok"
    assert last_route["route_reason"] == "local_direct_primary"


@pytest.mark.asyncio
async def test_route_query_passes_preferred_cloud_model_and_forces_cloud():
    """
    Если owner/web-path явно просит облачную модель, compat-роутер должен
    прокинуть её в OpenClawClient, а не молча оставить default primary.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())
    await router.route_query(
        "проверка cloud preferred",
        preferred_model="google-gemini-cli/gemini-3.1-pro-preview",
    )

    assert router.openclaw_client.last_call["preferred_model"] == "google-gemini-cli/gemini-3.1-pro-preview"
    assert router.openclaw_client.last_call["force_cloud"] is True


@pytest.mark.asyncio
async def test_route_query_preferred_local_model_overrides_force_cloud_mode():
    """
    Явный выбор локальной модели в owner UI должен быть сильнее общего
    force-cloud режима compat-роутера.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())
    router.force_mode = "force_cloud"

    await router.route_query(
        "проверка local preferred",
        preferred_model="nvidia/nemotron-3-nano",
    )

    assert router.openclaw_client.last_call["preferred_model"] == "nvidia/nemotron-3-nano"
    assert router.openclaw_client.last_call["force_cloud"] is False


def test_get_profile_recommendation_returns_ui_compatible_contract(monkeypatch):
    """
    Recommendation-контракт должен содержать поля, которые использует web UI:
    `model`, `recommended_model`, `channel`, `profile`.
    """
    # Изолируем от реального .env: force_cloud=False гарантирует local-first поведение
    monkeypatch.setattr(config, "FORCE_CLOUD", False)
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())

    recommend = router.get_profile_recommendation("chat")

    assert recommend["profile"] == "chat"
    assert recommend["model"] == "nvidia/nemotron-3-nano"
    assert recommend["recommended_model"] == "nvidia/nemotron-3-nano"
    assert recommend["channel"] == "local"


def test_local_preferred_model_restores_legacy_router_contract(monkeypatch):
    """
    Compat-роутер должен снова отдавать `local_preferred_model`, который ждут
    старые web write-endpoint'ы вроде `load-default`.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())
    previous = config.LOCAL_PREFERRED_MODEL
    monkeypatch.setattr(config, "LOCAL_PREFERRED_MODEL", "nvidia/nemotron-3-nano")

    try:
        assert router.local_preferred_model == "nvidia/nemotron-3-nano"
        router.local_preferred_model = "qwen3.5-9b-mlx"
        assert router.local_preferred_model == "qwen3.5-9b-mlx"
    finally:
        monkeypatch.setattr(config, "LOCAL_PREFERRED_MODEL", previous)


def test_router_init_survives_onboard_config_with_null_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Чистый `openclaw onboard` может оставить `agents.defaults.model = null`.
    Compat-роутер не должен падать на таком runtime-конфиге.
    """
    monkeypatch.setattr(mod.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(config, "MODEL", "google/gemini-2.5-flash")
    runtime_root = tmp_path / ".openclaw"
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "openclaw.json").write_text(
        json.dumps({"agents": {"defaults": {"model": None}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())

    assert router.models["chat"] == "google/gemini-2.5-flash"


def test_get_task_preflight_returns_rich_ui_payload():
    """
    Preflight должен возвращать богатый payload для кнопки `План (Preflight)`,
    а не старую минимальную заглушку.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())

    preflight = router.get_task_preflight(
        prompt="Сделай код-ревью и найди риски.",
        task_type="review",
        preferred_model="google/gemini-2.5-flash",
        confirm_expensive=False,
    )

    assert preflight["task_type"] == "review"
    assert preflight["profile"] == "review"
    assert preflight["execution"]["channel"] == "cloud"
    assert preflight["execution"]["model"] == "google/gemini-2.5-flash"
    assert isinstance(preflight["reasons"], list)
    assert isinstance(preflight["warnings"], list)
    assert "marginal_call_cost_usd" in preflight["cost_hint"]
    assert "next_step" in preflight


def test_feedback_summary_and_submit_restore_expected_contract():
    """
    Feedback API должен возвращать поля, которые уже ожидает фронт:
    `profile_model_stats`, `top_models`, `top_channels`, `total_feedback`.
    """
    router = WebRouterCompat(_FakeModelManager(), _FakeOpenClawClient())

    first = router.submit_feedback(
        score=5,
        profile="chat",
        model_name="nvidia/nemotron-3-nano",
        channel="local_direct",
        note="очень хорошо",
    )
    second = router.submit_feedback(
        score=3,
        profile="chat",
        model_name="nvidia/nemotron-3-nano",
        channel="local_direct",
        note="нормально",
    )
    summary = router.get_feedback_summary(profile="chat", top=5)

    assert first["model"] == "nvidia/nemotron-3-nano"
    assert first["profile"] == "chat"
    assert first["profile_model_stats"]["avg"] == 5.0
    assert second["profile_model_stats"]["avg"] == 4.0
    assert second["profile_model_stats"]["count"] == 2
    assert summary["profile"] == "chat"
    assert summary["total_feedback"] == 2
    assert summary["top_models"][0]["model"] == "nvidia/nemotron-3-nano"
    assert summary["top_models"][0]["avg_score"] == 4.0
    assert summary["top_channels"][0]["channel"] == "local_direct"


def test_ops_reports_restore_useful_cost_and_runway_contracts():
    """
    Ops endpoints должны получать полезный compat payload, а не пустые заглушки.
    """
    router = WebRouterCompat(
        _FakeModelManager(cost_analytics=_FakeCostAnalytics()),
        _FakeOpenClawClient(),
    )

    cost_report = router.get_cost_report(monthly_calls_forecast=5000)
    runway = router.get_credit_runway_report(
        credits_usd=300.0,
        horizon_days=80,
        reserve_ratio=0.1,
        monthly_calls_forecast=5000,
    )
    summary = router.get_ops_executive_summary(monthly_calls_forecast=5000)
    report = router.get_ops_report(history_limit=10, monthly_calls_forecast=5000)

    assert cost_report["status"] == "ok"
    assert cost_report["usage"]["tracked_calls"] == 30
    assert cost_report["costs"]["month_usd"] == 9.0
    assert runway["status"] == "ok"
    assert runway["runway_days"] is not None
    assert runway["safe_calls_per_day"] is not None
    assert summary["budget"]["cost_report_status"] == "ok"
    assert "runway_days" in summary["budget"]
    assert report["cost_report"]["status"] == "ok"
    assert report["runway"]["status"] == "ok"
    assert report["executive_summary"]["budget"]["month_usd"] == 9.0
