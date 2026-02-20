# -*- coding: utf-8 -*-
"""Тесты Phase D: профилирование, память выбора и cost guardrails."""

from pathlib import Path

from src.core.model_manager import ModelRouter


def _router(tmp_path: Path, soft_cap: int = 5) -> ModelRouter:
    return ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "CLOUD_SOFT_CAP_CALLS": str(soft_cap),
            "CLOUD_MONTHLY_BUDGET_USD": "25",
            "MONTHLY_CALLS_FORECAST": "5000",
        }
    )


def test_profile_classification(tmp_path: Path) -> None:
    router = _router(tmp_path)

    assert router.classify_task_profile("сделай security audit репозитория") == "security"
    assert router.classify_task_profile("настрой docker и deploy") == "infra"
    assert router.classify_task_profile("напиши python скрипт") == "code"
    assert router.classify_task_profile("обнови правила модерации чата") == "moderation"


def test_profile_memory_recommendation(tmp_path: Path) -> None:
    router = _router(tmp_path)

    router._remember_model_choice("code", "qwen2.5-coder-7b", "local")
    router._remember_model_choice("code", "qwen2.5-coder-7b", "local")
    router._remember_model_choice("code", "gemini-2.5-pro", "cloud")

    recommendation = router.get_profile_recommendation("code")
    assert recommendation["model"] == "qwen2.5-coder-7b"
    assert recommendation["channel"] == "local"


def test_feedback_submit_and_summary(tmp_path: Path) -> None:
    router = _router(tmp_path)
    router._remember_last_route(
        profile="chat",
        task_type="chat",
        channel="local",
        model_name="qwen2.5-7b",
        prompt="тестовый запрос",
    )

    result = router.submit_feedback(score=5, note="отличный ответ")
    assert result["ok"] is True
    assert result["used_last_route"] is True
    assert result["profile"] == "chat"
    assert result["model"] == "qwen2.5-7b"

    summary = router.get_feedback_summary(profile="chat")
    assert summary["total_feedback"] == 1
    assert summary["top_models"][0]["model"] == "qwen2.5-7b"
    assert summary["top_models"][0]["avg_score"] == 5.0


def test_feedback_influences_recommendation(tmp_path: Path) -> None:
    router = _router(tmp_path)
    for _ in range(3):
        router._remember_model_choice("chat", "model-a", "local")
        router._remember_model_choice("chat", "model-b", "local")

    for _ in range(3):
        router.submit_feedback(score=1, profile="chat", model_name="model-a", channel="local")
        router.submit_feedback(score=5, profile="chat", model_name="model-b", channel="local")

    recommendation = router.get_profile_recommendation("chat")
    assert recommendation["model"] == "model-b"
    assert recommendation["feedback_hint"]["avg_score"] >= 4.9
    assert recommendation["feedback_hint"]["count"] == 3


def test_last_route_keeps_reason_metadata(tmp_path: Path) -> None:
    router = _router(tmp_path)
    router._remember_last_route(
        profile="communication",
        task_type="chat",
        channel="cloud",
        model_name="gemini-2.5-pro",
        prompt="критичный запрос",
        route_reason="force_cloud",
        route_detail="forced by router mode",
        force_mode="force_cloud",
    )
    last_route = router.get_last_route()
    assert last_route["route_reason"] == "force_cloud"
    assert last_route["route_detail"] == "forced by router mode"
    assert last_route["force_mode"] == "force_cloud"


def test_heavy_light_tier_detection(tmp_path: Path) -> None:
    router = _router(tmp_path)

    assert router._model_tier("qwen2.5-coder-32b") == "heavy"
    assert router._model_tier("qwen2.5-coder-7b") == "light"


def test_lmstudio_loaded_detection_by_loaded_instances(tmp_path: Path) -> None:
    router = _router(tmp_path)
    assert router._is_lmstudio_model_loaded({"loaded_instances": [{"id": "proc1"}]}) is True
    assert router._is_lmstudio_model_loaded({"loaded_instances": []}) is False


def test_lmstudio_loaded_detection_by_state_fields(tmp_path: Path) -> None:
    router = _router(tmp_path)
    assert router._is_lmstudio_model_loaded({"state": "READY"}) is True
    assert router._is_lmstudio_model_loaded({"status": "loaded"}) is True
    assert router._is_lmstudio_model_loaded({"availability": "running"}) is True
    assert router._is_lmstudio_model_loaded({"state": "unloaded"}) is False


def test_cloud_error_detection_no_models_loaded_signature(tmp_path: Path) -> None:
    router = _router(tmp_path)
    assert router._is_cloud_error_message("400 No models loaded. Please load a model in the developer page.") is True
    assert router._is_cloud_error_message("The model has crashed without additional information. (Exit code: null)") is True


def test_cloud_soft_cap_switch(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=2)

    router._update_usage_report("review", "gemini-2.5-pro", "cloud")
    assert router.cloud_soft_cap_reached is False

    router._update_usage_report("review", "gemini-2.5-pro", "cloud")
    assert router.cloud_soft_cap_reached is True


def test_usage_summary_structure(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=10)
    router._update_usage_report("chat", "qwen2.5-7b", "local")
    router._update_usage_report("review", "gemini-2.5-pro", "cloud")

    summary = router.get_usage_summary()
    assert summary["totals"]["all_calls"] == 2
    assert summary["totals"]["local_calls"] == 1
    assert summary["totals"]["cloud_calls"] == 1
    assert summary["soft_cap"]["cloud_remaining_calls"] == 9
    assert len(summary["top_models"]) >= 1


def test_ops_alerts_cloud_share_high(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    for _ in range(20):
        router._update_usage_report("review", "gemini-2.5-pro", "cloud")

    alerts = router.get_ops_alerts()
    assert alerts["status"] == "alert"
    codes = {entry["code"] for entry in alerts["alerts"]}
    assert "cloud_share_high" in codes


def test_ops_alerts_model_quality_degraded(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    for _ in range(3):
        router.submit_feedback(score=1, profile="chat", model_name="bad-model", channel="local")
    alerts = router.get_ops_alerts()
    codes = {entry["code"] for entry in alerts["alerts"]}
    assert "model_quality_degraded" in codes


def test_ops_alert_ack_and_clear(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    for _ in range(20):
        router._update_usage_report("review", "gemini-2.5-pro", "cloud")

    ack_result = router.acknowledge_ops_alert("cloud_share_high", actor="tester", note="ok")
    assert ack_result["ok"] is True
    assert ack_result["code"] == "cloud_share_high"
    assert ack_result["ack"]["actor"] == "tester"

    alerts = router.get_ops_alerts()
    by_code = {entry["code"]: entry for entry in alerts["alerts"]}
    assert by_code["cloud_share_high"]["acknowledged"] is True
    assert by_code["cloud_share_high"]["ack"]["note"] == "ok"

    clear_result = router.clear_ops_alert_ack("cloud_share_high")
    assert clear_result["ok"] is True
    assert clear_result["removed"] is True

    alerts_after = router.get_ops_alerts()
    by_code_after = {entry["code"]: entry for entry in alerts_after["alerts"]}
    assert by_code_after["cloud_share_high"]["acknowledged"] is False


def test_ops_history_snapshot_growth(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    router._update_usage_report("chat", "qwen2.5-7b", "local")
    router.get_ops_alerts()
    router._update_usage_report("review", "gemini-2.5-pro", "cloud")
    router.get_ops_alerts()

    history = router.get_ops_history(limit=10)
    assert history["total"] >= 2
    assert history["count"] >= 2
    assert history["items"][-1]["status"] in {"ok", "alert"}


def test_cost_report_structure(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    for _ in range(3):
        router._update_usage_report("chat", "qwen2.5-7b", "local")
    for _ in range(2):
        router._update_usage_report("review", "gemini-2.5-pro", "cloud")

    report = router.get_cost_report(monthly_calls_forecast=1000)
    assert "costs_usd" in report
    assert "pricing" in report
    assert "monthly_forecast" in report
    assert report["monthly_forecast"]["forecast_calls"] == 1000
    assert report["costs_usd"]["total_cost"] >= 0
    assert "budget" in report


def test_ops_alerts_budget_exceeded_forecast(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "CLOUD_SOFT_CAP_CALLS": "10000",
            "CLOUD_COST_PER_CALL_USD": "0.05",
            "CLOUD_MONTHLY_BUDGET_USD": "10",
            "MONTHLY_CALLS_FORECAST": "1000",
        }
    )
    # Делаем cloud share близким к 1.0, чтобы прогноз вышел за бюджет.
    for _ in range(50):
        router._update_usage_report("review", "gemini-2.5-pro", "cloud")

    alerts = router.get_ops_alerts()
    codes = {entry["code"] for entry in alerts["alerts"]}
    assert "cloud_budget_exceeded_forecast" in codes


def test_ops_report_structure(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    router._update_usage_report("chat", "qwen2.5-7b", "local")
    router._update_usage_report("review", "gemini-2.5-pro", "cloud")
    report = router.get_ops_report(history_limit=5, monthly_calls_forecast=2000)

    assert "generated_at" in report
    assert "usage" in report
    assert "alerts" in report
    assert "costs" in report
    assert "history" in report
    assert report["costs"]["monthly_forecast"]["forecast_calls"] == 2000


def test_ops_history_prune_retention(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    router._ops_state["history"] = [
        {"ts": "2020-01-01T00:00:00+00:00", "status": "ok", "alerts_count": 0, "codes": []},
        {"ts": "2020-01-02T00:00:00+00:00", "status": "ok", "alerts_count": 0, "codes": []},
        {"ts": "2026-02-12T00:00:00+00:00", "status": "ok", "alerts_count": 0, "codes": []},
    ]
    result = router.prune_ops_history(max_age_days=30, keep_last=1)
    assert result["ok"] is True
    assert result["before"] == 3
    assert result["after"] == 1
    assert result["removed"] == 2


def test_ops_executive_summary_structure(tmp_path: Path) -> None:
    router = _router(tmp_path, soft_cap=100)
    for _ in range(3):
        router._update_usage_report("review", "gemini-2.5-pro", "cloud")
    for _ in range(2):
        router._update_usage_report("chat", "qwen2.5-7b", "local")
    summary = router.get_ops_executive_summary(monthly_calls_forecast=3000)

    assert "generated_at" in summary
    assert "risk_level" in summary
    assert "kpi" in summary
    assert "recommendations" in summary
    assert summary["kpi"]["calls_total"] == 5


def test_task_preflight_requires_confirm_for_critical_cloud(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "MODEL_REQUIRE_CONFIRM_EXPENSIVE": "1",
        }
    )
    plan = router.get_task_preflight(
        prompt="Проведи security audit продового API",
        task_type="security",
        confirm_expensive=False,
    )
    assert plan["critical"] is True
    assert plan["execution"]["channel"] == "cloud"
    assert plan["execution"]["requires_confirm_expensive"] is True
    assert plan["execution"]["can_run_now"] is False


def test_task_preflight_can_run_when_confirm_provided(tmp_path: Path) -> None:
    router = ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
            "MODEL_REQUIRE_CONFIRM_EXPENSIVE": "1",
        }
    )
    plan = router.get_task_preflight(
        prompt="Проведи security audit продового API",
        task_type="security",
        confirm_expensive=True,
    )
    assert plan["execution"]["requires_confirm_expensive"] is False
    assert plan["execution"]["can_run_now"] is True
    assert plan["execution"]["confirm_expensive_received"] is True


def test_route_explain_contains_last_route_reason(tmp_path: Path) -> None:
    router = _router(tmp_path)
    router._remember_last_route(
        profile="chat",
        task_type="chat",
        channel="local",
        model_name="qwen2.5-7b",
        route_reason="force_local",
        route_detail="forced by router mode",
    )
    explain = router.get_route_explain()
    assert explain["reason"]["code"] == "force_local"
    assert "принудительного режима" in explain["reason"]["human"]
    assert explain["explainability_score"] >= 70
    assert explain["transparency_level"] in {"medium", "high"}


def test_route_explain_includes_preflight_when_prompt_given(tmp_path: Path) -> None:
    router = _router(tmp_path)
    explain = router.get_route_explain(
        prompt="Проведи security audit API",
        task_type="security",
        confirm_expensive=True,
    )
    assert isinstance(explain["preflight"], dict)
    assert explain["preflight"]["task_type"] == "security"
    assert explain["explainability_score"] >= 20
