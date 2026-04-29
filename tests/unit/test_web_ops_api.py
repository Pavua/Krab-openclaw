# -*- coding: utf-8 -*-
"""
Тесты для ops/timeline API endpoints в web-панели Krab.

Покрываем маршруты:
  GET  /api/ops/diagnostics
  GET  /api/ops/metrics
  GET  /api/ops/timeline  (и алиас /api/timeline)
  GET  /api/ops/usage
  GET  /api/ops/cost-report
  GET  /api/ops/alerts
  GET  /api/ops/history
  GET  /api/ops/executive-summary
  GET  /api/ops/report
  POST /api/ops/maintenance/prune
  POST /api/ops/ack/{code}
  DEL  /api/ops/ack/{code}
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------

WEB_KEY = "test-secret-key"


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "model": "google/gemini-test", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True


class _FakeRouter:
    """Роутер с полным набором ops-методов."""

    _stats: dict = {"local_failures": 0, "cloud_failures": 0}
    _preflight_cache: dict = {}
    active_tier: str = "default"

    def get_model_info(self) -> dict:
        return {}

    def get_usage_summary(self) -> dict:
        return {"total_calls": 42, "local_calls": 10, "cloud_calls": 32}

    def get_cost_report(self, monthly_calls_forecast: int = 5000) -> dict:
        return {"estimated_cost_usd": 1.23, "monthly_calls_forecast": monthly_calls_forecast}

    def get_credit_runway_report(self, **_kwargs) -> dict:
        return {"runway_days": 80, "burn_rate_usd_day": 3.75}

    def get_ops_executive_summary(self, monthly_calls_forecast: int = 5000) -> dict:
        return {"kpi": {}, "risks": [], "recommendations": []}

    def get_ops_report(self, history_limit: int = 20, monthly_calls_forecast: int = 5000) -> dict:
        return {"usage": {}, "alerts": [], "history": []}

    def get_ops_alerts(self) -> list:
        return [{"code": "COST_HIGH", "severity": "warn", "message": "test"}]

    def get_ops_history(self, limit: int = 30) -> list:
        return [{"ts": "2026-04-12T00:00:00Z", "status": "ok"}]

    def prune_ops_history(self, max_age_days: int = 30, keep_last: int = 100) -> dict:
        return {"pruned": 0, "remaining": 1}

    def acknowledge_ops_alert(self, code: str, actor: str = "web_api", note: str = "") -> dict:
        return {"acked": True, "code": code, "actor": actor}

    def clear_ops_alert_ack(self, code: str) -> dict:
        return {"cleared": True, "code": code}

    async def check_local_health(self) -> bool:
        return True


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    """Создаёт TestClient с полным набором заглушек, включая web key."""
    deps = {
        "router": _FakeRouter(),
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
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18091, host="127.0.0.1")
    # Устанавливаем web_key для write-endpoints
    app._web_key = WEB_KEY
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# GET /api/ops/metrics
# ---------------------------------------------------------------------------


def test_ops_metrics_ok() -> None:
    """GET /api/ops/metrics должен вернуть ok=True и поле metrics."""
    fake_metrics = MagicMock()
    fake_metrics.get_snapshot.return_value = {"counters": {}, "latencies": {}}

    with patch("src.core.observability.metrics", fake_metrics):
        client = _make_client()
        resp = client.get("/api/ops/metrics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "metrics" in data


# ---------------------------------------------------------------------------
# GET /api/ops/timeline  и  GET /api/timeline
# ---------------------------------------------------------------------------


def test_ops_timeline_ok() -> None:
    """GET /api/ops/timeline возвращает ok=True и список events."""
    fake_timeline = MagicMock()
    fake_timeline.get_events.return_value = [{"ts": "2026-04-12T00:00:00Z", "event": "test"}]

    with patch("src.core.observability.timeline", fake_timeline):
        client = _make_client()
        resp = client.get("/api/ops/timeline")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["events"], list)


def test_api_timeline_alias_ok() -> None:
    """GET /api/timeline (короткий алиас) работает идентично /api/ops/timeline."""
    fake_timeline = MagicMock()
    fake_timeline.get_events.return_value = []

    with patch("src.core.observability.timeline", fake_timeline):
        client = _make_client()
        resp = client.get("/api/timeline")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ops_timeline_passes_params() -> None:
    """GET /api/ops/timeline передаёт параметры limit/min_severity/channel в get_events."""
    fake_timeline = MagicMock()
    fake_timeline.get_events.return_value = []

    # Патчим timeline и в web_app namespace, и в core.observability (после Wave E
    # endpoint вынесен в monitoring_router и берёт timeline из core.observability).
    with (
        patch("src.modules.web_app.timeline", fake_timeline),
        patch("src.core.observability.timeline", fake_timeline),
    ):
        client = _make_client()
        client.get("/api/ops/timeline?limit=5&min_severity=warn&channel=cloud")

    fake_timeline.get_events.assert_called_once_with(limit=5, min_severity="warn", channel="cloud")


# ---------------------------------------------------------------------------
# GET /api/ops/usage
# ---------------------------------------------------------------------------


def test_ops_usage_ok() -> None:
    """GET /api/ops/usage возвращает ok=True и данные usage из роутера."""
    client = _make_client()
    resp = client.get("/api/ops/usage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "usage" in data
    assert data["usage"]["total_calls"] == 42


def test_ops_usage_fallback_no_method() -> None:
    """Если у роутера нет get_usage_summary — возвращается ok=False с error."""
    deps = {
        "router": MagicMock(spec=[]),  # без get_usage_summary
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
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18092, host="127.0.0.1")
    client = TestClient(app.app)
    resp = client.get("/api/ops/usage")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/ops/alerts
# ---------------------------------------------------------------------------


def test_ops_alerts_returns_list() -> None:
    """GET /api/ops/alerts возвращает ok=True и список alerts."""
    client = _make_client()
    resp = client.get("/api/ops/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["alerts"], list)
    assert len(data["alerts"]) > 0


# ---------------------------------------------------------------------------
# GET /api/ops/history
# ---------------------------------------------------------------------------


def test_ops_history_default_limit() -> None:
    """GET /api/ops/history возвращает ok=True и список history."""
    client = _make_client()
    resp = client.get("/api/ops/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["history"], list)


def test_ops_history_custom_limit() -> None:
    """GET /api/ops/history?limit=5 передаёт limit в роутер."""
    router = _FakeRouter()
    deps = {
        "router": router,
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
        "kraab_userbot": _FakeKraab(),
    }
    from unittest.mock import patch as _patch

    with _patch.object(router, "get_ops_history", wraps=router.get_ops_history) as mocked:
        app = WebApp(deps, port=18093, host="127.0.0.1")
        client = TestClient(app.app)
        resp = client.get("/api/ops/history?limit=5")
        assert resp.status_code == 200
        mocked.assert_called_once_with(limit=5)


# ---------------------------------------------------------------------------
# POST /api/ops/maintenance/prune  (write-endpoint)
# ---------------------------------------------------------------------------


def test_ops_prune_with_auth() -> None:
    """POST /api/ops/maintenance/prune с корректным ключом возвращает ok=True."""
    client = _make_client()
    resp = client.post(
        "/api/ops/maintenance/prune",
        json={"max_age_days": 7, "keep_last": 50},
        headers={"X-Krab-Web-Key": WEB_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "result" in data


# ---------------------------------------------------------------------------
# POST /api/ops/ack/{code}
# ---------------------------------------------------------------------------


def test_ops_ack_alert() -> None:
    """POST /api/ops/ack/COST_HIGH подтверждает алерт и возвращает ok=True."""
    client = _make_client()
    resp = client.post(
        "/api/ops/ack/COST_HIGH",
        json={"actor": "admin", "note": "acknowledged in test"},
        headers={"X-Krab-Web-Key": WEB_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["acked"] is True


# ---------------------------------------------------------------------------
# DELETE /api/ops/ack/{code}
# ---------------------------------------------------------------------------


def test_ops_unack_alert() -> None:
    """DELETE /api/ops/ack/COST_HIGH снимает подтверждение и возвращает ok=True."""
    client = _make_client()
    resp = client.delete(
        "/api/ops/ack/COST_HIGH",
        headers={"X-Krab-Web-Key": WEB_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"]["cleared"] is True
