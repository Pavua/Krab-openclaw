# -*- coding: utf-8 -*-
"""Тесты WebApp: базовые API и устойчивость при rag=None."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _DummyRouter:
    def __init__(self):
        self.rag = None
        self._ack = {}
        self._feedback_events = []
        self._feedback_counter = 0
        self.models = {
            "chat": "google/gemini-2.5-flash",
            "thinking": "google/gemini-2.5-pro",
            "pro": "google/gemini-3-pro-preview",
            "coding": "openai/gpt-5-codex",
        }
        self.force_mode = "auto"
        self.local_engine = "lmstudio"
        self.active_local_model = "zai-org/glm-4.6v-flash"
        self.is_local_available = True
        self._history = [
            {
                "ts": "2026-02-12T10:00:00+00:00",
                "status": "ok",
                "alerts_count": 0,
                "codes": [],
                "cloud_calls": 3,
                "local_calls": 7,
            }
        ]

    async def check_local_health(self):
        return True

    def get_model_info(self):
        return {
            "local_model": self.active_local_model,
            "cloud_models": self.models.copy(),
            "force_mode": self.force_mode,
            "local_engine": self.local_engine,
            "local_available": self.is_local_available,
        }

    def set_force_mode(self, mode: str):
        if mode == "local":
            self.force_mode = "force_local"
        elif mode == "cloud":
            self.force_mode = "force_cloud"
        else:
            self.force_mode = "auto"
        return f"ok:{self.force_mode}"

    async def list_local_models_verbose(self):
        return [
            {
                "id": "zai-org/glm-4.6v-flash",
                "loaded": True,
                "type": "llm",
                "size_human": "7.09 GB",
            },
            {
                "id": "qwen/qwen3-coder-30b",
                "loaded": False,
                "type": "llm",
                "size_human": "18.2 GB",
            },
        ]

    def get_profile_recommendation(self, profile: str = "chat"):
        return {
            "profile": profile,
            "channel": "local",
            "model": "qwen2.5-7b",
            "critical": False,
        }

    def get_task_preflight(
        self,
        prompt: str,
        task_type: str = "chat",
        preferred_model: str | None = None,
        confirm_expensive: bool = False,
    ):
        return {
            "generated_at": "2026-02-12T21:00:00+00:00",
            "task_type": task_type,
            "profile": "chat",
            "critical": False,
            "prompt_preview": prompt[:240],
            "recommendation": self.get_profile_recommendation("chat"),
            "execution": {
                "channel": "local",
                "model": preferred_model or "qwen2.5-7b",
                "can_run_now": True,
                "requires_confirm_expensive": False,
                "confirm_expensive_received": bool(confirm_expensive),
            },
            "policy": {
                "routing_policy": "free_first_hybrid",
                "force_mode": "auto",
                "cloud_soft_cap_reached": False,
                "local_available": True,
            },
            "cost_hint": {
                "marginal_call_cost_usd": 0.0,
                "cloud_cost_per_call_usd": 0.01,
                "local_cost_per_call_usd": 0.0,
            },
            "warnings": [],
            "reasons": ["Стандартная policy free-first hybrid."],
            "next_step": "Можно запускать задачу.",
        }

    def get_route_explain(
        self,
        *,
        prompt: str = "",
        task_type: str = "chat",
        preferred_model: str | None = None,
        confirm_expensive: bool = False,
    ):
        preflight = None
        if prompt:
            preflight = self.get_task_preflight(
                prompt=prompt,
                task_type=task_type,
                preferred_model=preferred_model,
                confirm_expensive=confirm_expensive,
            )
        return {
            "generated_at": "2026-02-12T21:00:00+00:00",
            "last_route": {
                "profile": "chat",
                "task_type": "chat",
                "channel": "local",
                "model": "qwen2.5-7b",
                "route_reason": "local_primary",
                "route_detail": "local-first",
            },
            "reason": {
                "code": "local_primary",
                "detail": "local-first",
                "human": "Сработала стратегия local-first: локальная модель доступна.",
            },
            "policy": {
                "routing_policy": "free_first_hybrid",
                "force_mode": "auto",
                "cloud_soft_cap_reached": False,
                "local_available": True,
            },
            "preflight": preflight,
            "explainability_score": 90 if preflight else 70,
            "transparency_level": "high" if preflight else "medium",
        }

    def submit_feedback(
        self,
        score: int,
        profile: str | None = None,
        model_name: str | None = None,
        channel: str | None = None,
        note: str = "",
    ):
        value = int(score)
        if value < 1 or value > 5:
            raise ValueError("score_out_of_range_1_5")
        self._feedback_counter += 1
        payload = {
            "id": self._feedback_counter,
            "score": value,
            "profile": profile or "chat",
            "model": model_name or "qwen2.5-7b",
            "channel": channel or "local",
            "note": note,
        }
        self._feedback_events.append(payload)
        count = len(self._feedback_events)
        avg = round(sum(item["score"] for item in self._feedback_events) / count, 3)
        return {
            "ok": True,
            "score": value,
            "profile": payload["profile"],
            "model": payload["model"],
            "channel": payload["channel"],
            "used_last_route": profile is None and model_name is None,
            "profile_model_stats": {"count": count, "avg": avg},
            "profile_channel_stats": {"count": count, "avg": avg},
        }

    def get_feedback_summary(self, profile: str | None = None, top: int = 5):
        filtered = [
            item for item in self._feedback_events
            if not profile or item.get("profile") == profile
        ]
        count = len(filtered)
        avg = round(sum(item["score"] for item in filtered) / count, 3) if count else 0.0
        model = filtered[-1]["model"] if filtered else "qwen2.5-7b"
        channel = filtered[-1]["channel"] if filtered else "local"
        profile_name = profile or (filtered[-1]["profile"] if filtered else "chat")
        top_models = (
            [{"profile": profile_name, "model": model, "count": count, "avg_score": avg}]
            if count
            else []
        )
        top_channels = (
            [{"channel": channel, "count": count, "avg_score": avg}]
            if count
            else []
        )
        return {
            "generated_at": "2026-02-12T21:00:00+00:00",
            "profile": profile,
            "top_models": top_models[:top],
            "top_channels": top_channels,
            "total_feedback": count,
            "recent_events": filtered[-5:],
            "last_route": {
                "profile": "chat",
                "task_type": "chat",
                "channel": "local",
                "model": "qwen2.5-7b",
            },
        }

    def get_last_route(self):
        return {
            "profile": "chat",
            "task_type": "chat",
            "channel": "local",
            "model": "qwen2.5-7b",
        }

    def get_usage_summary(self):
        return {
            "totals": {"all_calls": 10, "local_calls": 7, "cloud_calls": 3},
            "ratios": {"local_share": 0.7, "cloud_share": 0.3},
            "soft_cap": {
                "cloud_soft_cap_calls": 100,
                "cloud_soft_cap_reached": False,
                "cloud_remaining_calls": 97,
            },
            "top_models": [{"model": "qwen2.5-7b", "count": 6}],
            "top_profiles": [{"profile": "chat", "count": 8}],
        }

    def get_ops_alerts(self):
        return {
            "status": "ok",
            "alerts": [],
            "summary": self.get_usage_summary(),
        }

    def get_cost_report(self, monthly_calls_forecast: int = 5000):
        return {
            "costs_usd": {
                "cloud_calls_cost": 0.03,
                "local_calls_cost": 0.0,
                "total_cost": 0.03,
                "avg_cost_per_call": 0.003,
            },
            "pricing": {
                "cloud_cost_per_call_usd": 0.01,
                "local_cost_per_call_usd": 0.0,
            },
            "monthly_forecast": {
                "forecast_calls": monthly_calls_forecast,
                "forecast_cloud_calls": 1500,
                "forecast_local_calls": 3500,
                "forecast_cloud_cost": 15.0,
                "forecast_local_cost": 0.0,
                "forecast_total_cost": 15.0,
            },
            "usage_summary": self.get_usage_summary(),
        }

    def get_ops_report(self, history_limit: int = 20, monthly_calls_forecast: int = 5000):
        return {
            "generated_at": "2026-02-12T21:00:00+00:00",
            "usage": self.get_usage_summary(),
            "alerts": self.get_ops_alerts(),
            "costs": self.get_cost_report(monthly_calls_forecast=monthly_calls_forecast),
            "history": self.get_ops_history(limit=history_limit),
        }

    def get_ops_executive_summary(self, monthly_calls_forecast: int = 5000):
        return {
            "generated_at": "2026-02-12T21:00:00+00:00",
            "risk_level": "low",
            "kpi": {
                "calls_total": 10,
                "cloud_share": 0.3,
                "forecast_total_cost": 15.0,
                "budget_ratio": 0.6,
                "active_alerts": 0,
            },
            "alerts_brief": [],
            "recommendations": ["Контур стабильный: поддерживать текущую policy и мониторинг."],
        }

    def get_credit_runway_report(
        self,
        credits_usd: float = 300.0,
        horizon_days: int = 80,
        reserve_ratio: float = 0.1,
        monthly_calls_forecast: int = 5000,
    ):
        return {
            "credits_usd": float(credits_usd),
            "horizon_days": int(horizon_days),
            "reserve_ratio": float(reserve_ratio),
            "daily_target_budget_usd": 3.0,
            "estimated_daily_burn_usd": 1.0,
            "runway_days_at_current_burn": 300.0,
            "recommended_calls_per_day": 100,
            "scenarios": {
                "flash_lite": {"unit_cost_usd": 0.007, "max_calls_per_day": 400},
                "flash": {"unit_cost_usd": 0.01, "max_calls_per_day": 300},
                "pro": {"unit_cost_usd": 0.03, "max_calls_per_day": 100},
            },
            "forecast_calls_monthly": int(monthly_calls_forecast),
            "cost_report": self.get_cost_report(monthly_calls_forecast=monthly_calls_forecast),
        }

    def get_ops_history(self, limit: int = 30):
        items = self._history[-max(1, int(limit)) :]
        return {
            "items": items,
            "count": len(items),
            "total": len(self._history),
        }

    def prune_ops_history(self, max_age_days: int = 30, keep_last: int = 100):
        before = len(self._history)
        # Dummy behavior для теста endpoint
        self._history = self._history[-max(1, int(keep_last)) :]
        after = len(self._history)
        return {
            "ok": True,
            "before": before,
            "after": after,
            "removed": max(0, before - after),
            "max_age_days": int(max_age_days),
            "keep_last": int(keep_last),
        }

    def acknowledge_ops_alert(self, code: str, actor: str = "owner", note: str = ""):
        if not code:
            raise ValueError("code_required")
        payload = {"ts": "2026-02-12T10:01:00+00:00", "actor": actor, "note": note}
        self._ack[code] = payload
        return {"ok": True, "code": code, "ack": payload}

    def clear_ops_alert_ack(self, code: str):
        if not code:
            raise ValueError("code_required")
        existed = code in self._ack
        self._ack.pop(code, None)
        return {"ok": True, "code": code, "removed": existed}

    def classify_task_profile(self, prompt: str, task_type: str = "chat"):
        if "code" in (prompt or "").lower() or task_type == "coding":
            return "code"
        return "chat"

    async def route_query(
        self,
        prompt: str,
        task_type: str = "chat",
        context=None,
        chat_type: str = "private",
        is_owner: bool = True,
        use_rag: bool = False,
        preferred_model=None,
        confirm_expensive: bool = False,
    ):
        return f"reply::{task_type}::{prompt[:20]}"


class _DummyBlackBox:
    def __init__(self):
        self.events = []

    def get_stats(self):
        return {"total": 42}

    def log_event(self, event_type: str, details: str):
        self.events.append((event_type, details))


class _DummyOpenClaw:
    async def health_check(self):
        return True

    async def get_health_report(self):
        return {
            "gateway": True,
            "auth": {"available": True, "ready_for_subscriptions": True},
            "browser": {"available": True},
            "tools": {"available": True, "tools_count": 2},
            "ready_for_subscriptions": True,
            "base_url": "http://localhost:18789",
        }

    async def get_deep_health_report(self):
        return {
            "ready": True,
            "issues": [],
            "remediations": [],
            "tool_smoke": {"ok": True, "tool": "web_search"},
            "base": await self.get_health_report(),
        }

    async def get_remediation_plan(self):
        return {
            "ready": True,
            "issues": [],
            "open_items": 0,
            "steps": [
                {
                    "priority": "P3",
                    "id": "no_action_needed",
                    "title": "Критичных проблем не обнаружено",
                    "done": True,
                    "action": "None",
                }
            ],
        }

    async def get_browser_smoke_report(self, url: str = "https://example.com"):
        return {
            "base": await self.get_health_report(),
            "browser_smoke": {
                "ok": True,
                "channel": "endpoint",
                "url": url,
                "endpoint_attempts": [{"path": "/v1/browser/smoke", "ok": True, "status": 200}],
                "tool_attempts": [],
            },
            "ready": True,
        }

    async def get_cloud_provider_diagnostics(self, providers=None):
        providers_list = providers or ["google", "openai"]
        result = {}
        for provider in providers_list:
            name = str(provider).lower()
            result[name] = {
                "ok": True,
                "error_code": "",
                "summary": "",
                "retryable": False,
                "key_source": f"env:{name.upper()}_API_KEY",
                "key_preview": "AIza****",
                "hint": "",
            }
        return {
            "providers": result,
            "timestamp": "2026-02-22T00:00:00+00:00",
        }


class _DummyVoiceGateway:
    async def health_check(self):
        return False


class _DummyKrabEar:
    async def health_check(self):
        return False


class _DummyProvisioning:
    def __init__(self):
        self.created = 0
        self.applied = 0

    def list_templates(self, entity):
        return [{"id": "coding_agent", "entity": entity}]

    def list_drafts(self, limit=20, status=None):
        return [{"id": "draft-1", "status": status or "draft", "limit": limit}]

    def create_draft(self, **kwargs):
        self.created += 1
        return {"id": "draft-2", **kwargs}

    def preview_diff(self, draft_id):
        if draft_id != "draft-1":
            raise ValueError("draft_not_found")
        return {"draft_id": draft_id, "changes": ["+ item"]}

    def apply_draft(self, draft_id, confirmed=False):
        if not confirmed:
            raise ValueError("confirm_required")
        self.applied += 1
        return {"draft_id": draft_id, "applied": True}


def _build_client_with_deps() -> tuple[TestClient, dict]:
    bb = _DummyBlackBox()
    provisioning = _DummyProvisioning()
    deps = {
        "router": _DummyRouter(),
        "black_box": bb,
        "openclaw_client": _DummyOpenClaw(),
        "voice_gateway_client": _DummyVoiceGateway(),
        "krab_ear_client": _DummyKrabEar(),
        "provisioning_service": provisioning,
    }
    app = WebApp(deps=deps, port=8080)
    return TestClient(app.app), deps


def _build_client() -> TestClient:
    client, _ = _build_client_with_deps()
    return client


def test_stats_endpoint_works_without_rag() -> None:
    client = _build_client()
    response = client.get("/api/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["black_box"]["total"] == 42
    assert payload["rag"]["enabled"] is False


def test_health_endpoint_reports_chain_state() -> None:
    client = _build_client()
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["openclaw"] is True
    assert payload["checks"]["local_lm"] is True
    assert payload["checks"]["voice_gateway"] is False
    assert payload["checks"]["krab_ear"] is False
    assert payload["degradation"] == "normal"
    assert payload["risk_level"] in {"low", "medium", "high"}
    assert payload["chain"]["active_ai_channel"] == "cloud"


def test_transcriber_status_endpoint_reports_down_state(monkeypatch) -> None:
    monkeypatch.setenv("STT_ISOLATED_WORKER", "1")
    monkeypatch.setenv("STT_WORKER_TIMEOUT_SECONDS", "240")
    client = _build_client()
    response = client.get("/api/transcriber/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    status = payload["status"]
    assert status["readiness"] == "down"
    assert status["voice_gateway_ok"] is False
    assert status["stt_isolated_worker"] is True
    assert "transcriber_doctor.command --heal" in " ".join(status["recommendations"])
    monkeypatch.delenv("STT_ISOLATED_WORKER", raising=False)
    monkeypatch.delenv("STT_WORKER_TIMEOUT_SECONDS", raising=False)


def test_transcriber_status_endpoint_respects_perceptor_config(monkeypatch) -> None:
    class _VoiceGatewayUp:
        async def health_check(self):
            return True

    class _PerceptorStub:
        whisper_model = "mlx-community/whisper-large-v3-turbo"
        stt_isolated_worker = False

    monkeypatch.setenv("STT_ISOLATED_WORKER", "1")
    client, deps = _build_client_with_deps()
    deps["voice_gateway_client"] = _VoiceGatewayUp()
    deps["perceptor"] = _PerceptorStub()

    response = client.get("/api/transcriber/status")
    assert response.status_code == 200
    payload = response.json()
    status = payload["status"]
    assert status["voice_gateway_ok"] is True
    assert status["stt_isolated_worker"] is False
    assert status["readiness"] == "degraded"
    assert status["whisper_model"] == "mlx-community/whisper-large-v3-turbo"
    assert "STT_ISOLATED_WORKER=1" in " ".join(status["recommendations"])
    monkeypatch.delenv("STT_ISOLATED_WORKER", raising=False)


def test_ecosystem_health_endpoint_reports_details() -> None:
    client = _build_client()
    response = client.get("/api/ecosystem/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    report = payload["report"]
    assert report["degradation"] == "normal"
    assert report["checks"]["openclaw"]["ok"] is True
    assert report["checks"]["local_lm"]["ok"] is True
    assert report["checks"]["voice_gateway"]["ok"] is False
    assert report["checks"]["krab_ear"]["ok"] is False
    assert report["chain"]["fallback_ready"] is True
    assert isinstance(report["recommendations"], list)

    export_response = client.get("/api/ecosystem/health/export")
    assert export_response.status_code == 200
    assert "application/json" in export_response.headers.get("content-type", "")


def test_model_recommend_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/model/recommend?profile=security")
    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] == "security"
    assert payload["model"] == "qwen2.5-7b"


def test_model_preflight_endpoint() -> None:
    client = _build_client()
    response = client.post(
        "/api/model/preflight",
        json={
            "prompt": "Проведи аудит API безопасности",
            "task_type": "security",
            "confirm_expensive": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    preflight = payload["preflight"]
    assert preflight["task_type"] == "security"
    assert preflight["execution"]["confirm_expensive_received"] is True


def test_model_explain_endpoint_without_prompt() -> None:
    client = _build_client()
    response = client.get("/api/model/explain")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    explain = payload["explain"]
    assert explain["reason"]["code"] == "local_primary"
    assert explain["preflight"] is None
    assert explain["transparency_level"] in {"medium", "high"}


def test_model_explain_endpoint_with_prompt() -> None:
    client = _build_client()
    response = client.get(
        "/api/model/explain",
        params={
            "task_type": "security",
            "prompt": "Проведи security аудит API",
            "confirm_expensive": "true",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    explain = payload["explain"]
    assert isinstance(explain["preflight"], dict)
    assert explain["preflight"]["task_type"] == "security"
    assert explain["preflight"]["execution"]["confirm_expensive_received"] is True


def test_model_feedback_summary_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/model/feedback?profile=chat&top=3")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["feedback"]["profile"] == "chat"


def test_model_feedback_submit_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post("/api/model/feedback", json={"score": 5})
    assert denied.status_code == 403

    ok = client.post(
        "/api/model/feedback",
        json={"score": 5, "profile": "chat", "model": "qwen2.5-7b", "channel": "local"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["ok"] is True
    assert payload["result"]["score"] == 5
    assert payload["result"]["profile"] == "chat"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_model_feedback_submit_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()
    headers = {
        "X-Krab-Web-Key": "secret123",
        "X-Idempotency-Key": "feedback-dup-1",
    }
    first = client.post("/api/model/feedback", json={"score": 4, "profile": "chat"}, headers=headers)
    assert first.status_code == 200
    assert first.json().get("idempotent_replay") is None

    second = client.post("/api/model/feedback", json={"score": 4, "profile": "chat"}, headers=headers)
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_openclaw_report_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/report")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"]["ready_for_subscriptions"] is True


def test_openclaw_deep_check_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/deep-check")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"]["ready"] is True
    assert payload["report"]["tool_smoke"]["ok"] is True


def test_openclaw_remediation_plan_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/remediation-plan")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"]["ready"] is True
    assert payload["report"]["steps"][0]["id"] == "no_action_needed"


def test_openclaw_browser_smoke_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/browser-smoke?url=https://example.com")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"]["ready"] is True
    assert payload["report"]["browser_smoke"]["ok"] is True


def test_openclaw_cloud_diagnostics_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/cloud")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["report"]["providers"]["google"]["ok"] is True
    assert payload["report"]["providers"]["openai"]["ok"] is True


def test_openclaw_cloud_diagnostics_endpoint_with_providers_filter() -> None:
    client = _build_client()
    response = client.get("/api/openclaw/cloud?providers=google")
    assert response.status_code == 200
    payload = response.json()
    providers = payload["report"]["providers"]
    assert set(providers.keys()) == {"google"}


def test_openclaw_cloud_diagnostics_endpoint_not_supported() -> None:
    client, deps = _build_client_with_deps()
    deps["openclaw_client"] = object()
    response = client.get("/api/openclaw/cloud")
    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["error"] == "cloud_diagnostics_not_supported"


def test_openclaw_model_autoswitch_status_endpoint(monkeypatch) -> None:
    client = _build_client()

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"ok": true, "lm_loaded": false, "desired_default": "google/gemini-2.5-flash", '
        '"applied": false, "dry_run": true}\n'
    )
    completed.stderr = ""

    with patch("src.modules.web_app.subprocess.run", return_value=completed) as mocked_run:
        response = client.get("/api/openclaw/model-autoswitch/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["autoswitch"]["desired_default"] == "google/gemini-2.5-flash"
    assert payload["autoswitch"]["dry_run"] is True
    mocked_run.assert_called_once()


def test_openclaw_model_autoswitch_apply_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post("/api/openclaw/model-autoswitch/apply")
    assert denied.status_code == 403

    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = (
        '{"ok": true, "lm_loaded": true, "desired_default": "lmstudio/local", '
        '"applied": true, "dry_run": false}\n'
    )
    completed.stderr = ""

    with patch("src.modules.web_app.subprocess.run", return_value=completed):
        ok = client.post(
            "/api/openclaw/model-autoswitch/apply",
            headers={"X-Krab-Web-Key": "secret123"},
        )

    assert ok.status_code == 200
    payload = ok.json()
    assert payload["ok"] is True
    assert payload["autoswitch"]["desired_default"] == "lmstudio/local"
    assert payload["autoswitch"]["applied"] is True
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_ops_usage_and_alerts_endpoints() -> None:
    client = _build_client()

    usage_response = client.get("/api/ops/usage")
    assert usage_response.status_code == 200
    usage_payload = usage_response.json()
    assert usage_payload["ok"] is True
    assert usage_payload["usage"]["totals"]["all_calls"] == 10

    alerts_response = client.get("/api/ops/alerts")
    assert alerts_response.status_code == 200
    alerts_payload = alerts_response.json()
    assert alerts_payload["ok"] is True
    assert alerts_payload["alerts"]["status"] == "ok"

    cost_response = client.get("/api/ops/cost-report?monthly_calls_forecast=8000")
    assert cost_response.status_code == 200
    cost_payload = cost_response.json()
    assert cost_payload["ok"] is True
    assert cost_payload["report"]["monthly_forecast"]["forecast_calls"] == 8000

    executive_response = client.get("/api/ops/executive-summary?monthly_calls_forecast=9000")
    assert executive_response.status_code == 200
    executive_payload = executive_response.json()
    assert executive_payload["ok"] is True
    assert executive_payload["summary"]["kpi"]["calls_total"] == 10

    runway_response = client.get(
        "/api/ops/runway?credits_usd=300&horizon_days=80&reserve_ratio=0.1&monthly_calls_forecast=9000"
    )
    assert runway_response.status_code == 200
    runway_payload = runway_response.json()
    assert runway_payload["ok"] is True
    assert runway_payload["runway"]["credits_usd"] == 300.0
    assert runway_payload["runway"]["horizon_days"] == 80
    assert runway_payload["runway"]["forecast_calls_monthly"] == 9000

    report_response = client.get("/api/ops/report?history_limit=5&monthly_calls_forecast=9000")
    assert report_response.status_code == 200
    report_payload = report_response.json()
    assert report_payload["ok"] is True
    assert report_payload["report"]["costs"]["monthly_forecast"]["forecast_calls"] == 9000
    assert report_payload["report"]["history"]["count"] == 1

    export_response = client.get("/api/ops/report/export?history_limit=5&monthly_calls_forecast=9000")
    assert export_response.status_code == 200
    assert "application/json" in export_response.headers.get("content-type", "")
    assert "\"generated_at\"" in export_response.text

    bundle_response = client.get("/api/ops/bundle?history_limit=5&monthly_calls_forecast=9000")
    assert bundle_response.status_code == 200
    bundle_payload = bundle_response.json()
    assert bundle_payload["ok"] is True
    assert bundle_payload["bundle"]["ops_report"]["costs"]["monthly_forecast"]["forecast_calls"] == 9000

    bundle_export_response = client.get("/api/ops/bundle/export?history_limit=5&monthly_calls_forecast=9000")
    assert bundle_export_response.status_code == 200
    assert "application/json" in bundle_export_response.headers.get("content-type", "")
    assert "\"ops_report\"" in bundle_export_response.text


def test_ops_history_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/ops/history?limit=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["history"]["count"] == 1
    assert payload["history"]["items"][0]["status"] == "ok"


def test_ops_ack_and_unack_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post("/api/ops/ack/cloud_share_high", json={"actor": "web"})
    assert denied.status_code == 403

    ack = client.post(
        "/api/ops/ack/cloud_share_high",
        json={"actor": "web", "note": "accepted"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert ack.status_code == 200
    ack_payload = ack.json()
    assert ack_payload["ok"] is True
    assert ack_payload["result"]["code"] == "cloud_share_high"

    unack = client.delete(
        "/api/ops/ack/cloud_share_high",
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert unack.status_code == 200
    unack_payload = unack.json()
    assert unack_payload["ok"] is True
    assert unack_payload["result"]["removed"] is True

    prune_denied = client.post(
        "/api/ops/maintenance/prune",
        json={"max_age_days": 7, "keep_last": 1},
    )
    assert prune_denied.status_code == 403

    prune_ok = client.post(
        "/api/ops/maintenance/prune",
        json={"max_age_days": 7, "keep_last": 1},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert prune_ok.status_code == 200
    prune_payload = prune_ok.json()
    assert prune_payload["ok"] is True
    assert prune_payload["result"]["max_age_days"] == 7
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_provisioning_write_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()
    response = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "A"},
    )
    assert response.status_code == 403

    response_ok = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "A"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert response_ok.status_code == 200
    payload = response_ok.json()
    assert payload["ok"] is True
    assert payload["draft"]["entity_type"] == "agent"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_assistant_query_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post("/api/assistant/query", json={"prompt": "hello"})
    assert denied.status_code == 403

    ok = client.post(
        "/api/assistant/query",
        json={"prompt": "hello world", "task_type": "chat", "use_rag": False},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["ok"] is True
    assert payload["mode"] == "web_native"
    assert payload["task_type"] == "chat"
    assert "reply::chat::hello world" in payload["reply"]
    assert payload["last_route"]["model"] == "qwen2.5-7b"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_assistant_capabilities_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/assistant/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "web_native"
    assert payload["endpoint"] == "/api/assistant/query"
    assert payload["feedback_endpoint"] == "/api/model/feedback"
    assert payload["model_catalog_endpoint"] == "/api/model/catalog"
    assert payload["model_apply_endpoint"] == "/api/model/apply"
    assert payload["attachment_endpoint"] == "/api/assistant/attachment"


def test_assistant_attachment_upload(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post(
        "/api/assistant/attachment",
        files={"file": ("note.txt", "Привет, Краб!".encode("utf-8"), "text/plain")},
    )
    assert denied.status_code == 403

    ok = client.post(
        "/api/assistant/attachment",
        files={"file": ("note.txt", "Привет, Краб!".encode("utf-8"), "text/plain")},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["ok"] is True
    attachment = payload["attachment"]
    assert attachment["kind"] == "text"
    assert attachment["has_extracted_text"] is True
    assert "Контекст из файла `note.txt`" in attachment["prompt_snippet"]
    assert str(attachment["stored_path"]).startswith("artifacts/web_uploads/")

    stored_path = Path(str(attachment["stored_path"]))
    if stored_path.exists():
        stored_path.unlink()
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_model_catalog_endpoint() -> None:
    client = _build_client()
    response = client.get("/api/model/catalog")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    catalog = payload["catalog"]
    assert catalog["force_mode"] == "auto"
    assert "chat" in catalog["slots"]
    assert len(catalog["local_models"]) >= 1


def test_model_apply_endpoint(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()

    denied = client.post("/api/model/apply", json={"action": "set_mode", "mode": "local"})
    assert denied.status_code == 403

    set_mode = client.post(
        "/api/model/apply",
        json={"action": "set_mode", "mode": "local"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert set_mode.status_code == 200
    set_mode_payload = set_mode.json()
    assert set_mode_payload["ok"] is True
    assert set_mode_payload["catalog"]["force_mode"] == "local"

    set_slot = client.post(
        "/api/model/apply",
        json={"action": "set_slot_model", "slot": "chat", "model": "gpt-5-mini"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert set_slot.status_code == 200
    set_slot_payload = set_slot.json()
    assert set_slot_payload["ok"] is True
    assert set_slot_payload["catalog"]["cloud_slots"]["chat"] == "openai/gpt-5-mini"

    apply_preset = client.post(
        "/api/model/apply",
        json={"action": "apply_preset", "preset": "balanced_auto"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert apply_preset.status_code == 200
    preset_payload = apply_preset.json()
    assert preset_payload["ok"] is True
    assert preset_payload["catalog"]["force_mode"] == "auto"
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_assistant_rate_limit(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    monkeypatch.setenv("WEB_ASSISTANT_RATE_LIMIT_PER_MIN", "1")
    client = _build_client()

    first = client.post(
        "/api/assistant/query",
        json={"prompt": "one", "task_type": "chat"},
        headers={"X-Krab-Web-Key": "secret123", "X-Krab-Client": "client-a"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/assistant/query",
        json={"prompt": "two", "task_type": "chat"},
        headers={"X-Krab-Web-Key": "secret123", "X-Krab-Client": "client-a"},
    )
    assert second.status_code == 429
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    monkeypatch.delenv("WEB_ASSISTANT_RATE_LIMIT_PER_MIN", raising=False)


def test_web_audit_events(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client, deps = _build_client_with_deps()

    response1 = client.post(
        "/api/assistant/query",
        json={"prompt": "audit me", "task_type": "chat"},
        headers={"X-Krab-Web-Key": "secret123", "X-Krab-Client": "audit-client"},
    )
    assert response1.status_code == 200

    response2 = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "A"},
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert response2.status_code == 200

    response3 = client.post(
        "/api/provisioning/apply/draft-1?confirm=true",
        headers={"X-Krab-Web-Key": "secret123"},
    )
    assert response3.status_code == 200

    events = [item[0] for item in deps["black_box"].events]
    assert "web_assistant_query" in events
    assert "web_provisioning_draft_create" in events
    assert "web_provisioning_apply" in events
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_assistant_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client = _build_client()
    headers = {
        "X-Krab-Web-Key": "secret123",
        "X-Krab-Client": "idem-client",
        "X-Idempotency-Key": "same-req-1",
    }

    first = client.post("/api/assistant/query", json={"prompt": "idempotent", "task_type": "chat"}, headers=headers)
    assert first.status_code == 200
    assert first.json().get("idempotent_replay") is None

    second = client.post("/api/assistant/query", json={"prompt": "idempotent", "task_type": "chat"}, headers=headers)
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_provisioning_idempotency(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret123")
    client, deps = _build_client_with_deps()
    headers = {
        "X-Krab-Web-Key": "secret123",
        "X-Idempotency-Key": "draft-dup-1",
    }

    first = client.post("/api/provisioning/drafts", json={"entity_type": "agent", "name": "A"}, headers=headers)
    assert first.status_code == 200
    second = client.post("/api/provisioning/drafts", json={"entity_type": "agent", "name": "A"}, headers=headers)
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True
    assert deps["provisioning_service"].created == 1

    apply_headers = {
        "X-Krab-Web-Key": "secret123",
        "X-Idempotency-Key": "apply-dup-1",
    }
    first_apply = client.post("/api/provisioning/apply/draft-1?confirm=true", headers=apply_headers)
    assert first_apply.status_code == 200
    second_apply = client.post("/api/provisioning/apply/draft-1?confirm=true", headers=apply_headers)
    assert second_apply.status_code == 200
    assert second_apply.json().get("idempotent_replay") is True
    assert deps["provisioning_service"].applied == 1
    monkeypatch.delenv("WEB_API_KEY", raising=False)


def test_nano_theme_css_route_available() -> None:
    """Панель должна отдавать CSS темы по каноничному URL."""
    client = _build_client()
    response = client.get("/nano_theme.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert ":root" in response.text


def test_nano_theme_css_legacy_route_available() -> None:
    """Старый URL темы тоже должен работать для обратной совместимости."""
    client = _build_client()
    response = client.get("/prototypes/nano/nano_theme.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert "--bg-main" in response.text
