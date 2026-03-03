# -*- coding: utf-8 -*-
"""
Тесты runtime endpoint'ов web-панели.

Покрываем:
1) расширенный `/api/health/lite`;
2) `GET /api/runtime/handoff`;
3) `POST /api/runtime/recover` (guard + успешный dry-like запуск).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp


class _DummyRouter:
    """Минимальный роутер-заглушка для инициализации WebApp."""

    def get_model_info(self):
        return {}


class _FakeOpenClaw:
    """Фейковый OpenClaw клиент для детерминированных тестов runtime endpoint'ов."""

    async def health_check(self) -> bool:
        return True

    def get_last_runtime_route(self):
        return {
            "channel": "local_direct",
            "provider": "nvidia",
            "model": "nvidia/nemotron-3-nano",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self):
        return {
            "active_tier": "free",
            "last_error_code": None,
            "last_provider_status": "ok",
            "last_recovery_action": "none",
        }

    async def get_cloud_runtime_check(self):
        return {"ok": True, "provider": "google", "active_tier": "free"}

    async def switch_cloud_tier(self, tier: str):
        return {"ok": True, "new_tier": tier}


class _FakeHealthClient:
    """Фейковый клиент сервиса с `health_check`."""

    def __init__(self, ok: bool = True):
        self._ok = ok

    async def health_check(self) -> bool:
        return self._ok


def _make_client(*, openclaw_client=None) -> TestClient:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": openclaw_client or _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(ok=True),
        "krab_ear_client": _FakeHealthClient(ok=True),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


def test_health_lite_contains_runtime_fields(monkeypatch):
    """
    `/api/health/lite` должен содержать новые runtime-поля,
    даже если внешний контур (LM Studio) в тесте недоступен.
    """
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "up"
    assert "telegram_session_state" in data
    assert "lmstudio_model_state" in data
    assert "openclaw_auth_state" in data
    assert "last_runtime_route" in data


def test_runtime_handoff_returns_machine_readable_snapshot(monkeypatch):
    """`/api/runtime/handoff` должен отдавать единый JSON-снимок для anti-413 handoff."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.get("/api/runtime/handoff")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "generated_at_utc" in data
    assert "git" in data
    assert "runtime" in data
    assert "services" in data
    assert "artifacts" in data
    assert data["health_lite"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"


def test_runtime_recover_requires_web_api_key(monkeypatch):
    """Write endpoint `/api/runtime/recover` должен быть закрыт WEB_API_KEY при включенной защите."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    client = _make_client()

    resp = client.post("/api/runtime/recover", json={})
    assert resp.status_code == 403


def test_runtime_recover_minimal_flow(monkeypatch):
    """
    Минимальный recovery flow без запуска скриптов:
    endpoint должен отработать и вернуть post-check runtime.
    """
    monkeypatch.setenv("WEB_API_KEY", "secret")
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client()

    resp = client.post(
        "/api/runtime/recover",
        json={
            "run_openclaw_runtime_repair": False,
            "run_sync_openclaw_models": False,
            "force_tier": "free",
            "probe_cloud_runtime": True,
        },
        headers={"X-Krab-Web-Key": "secret"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["steps"], list)
    assert data["runtime_after"]["last_runtime_route"]["model"] == "nvidia/nemotron-3-nano"
    assert data["cloud_runtime"]["available"] is True


def test_parse_openclaw_channels_probe_returns_normalized_channels():
    """Парсер channels probe должен отдавать нормализованный список каналов для UI."""
    sample = """
Checking channel status (probe)…
Gateway reachable.
- Telegram default: enabled, configured, running, works
- BlueBubbles default: enabled, not configured, stopped, disconnected, error:not configured

Warnings:
- bluebubbles default: Not configured
""".strip()

    parsed = WebApp._parse_openclaw_channels_probe(sample)
    assert parsed["gateway_reachable"] is True
    assert len(parsed["channels"]) == 2
    assert parsed["channels"][0]["name"] == "Telegram default"
    assert parsed["channels"][0]["status"] == "OK"
    assert parsed["channels"][1]["name"] == "BlueBubbles default"
    assert parsed["channels"][1]["status"] == "FAIL"
    assert parsed["warnings"] == ["bluebubbles default: Not configured"]


def test_health_lite_marks_auth_unauthorized_when_provider_reports_auth(monkeypatch):
    """`health/lite` должен показывать unauthorized при provider_status=auth."""

    class _OpenClawAuthState(_FakeOpenClaw):
        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": None,
                "last_provider_status": "auth",
                "last_recovery_action": "switch_provider_or_key",
            }

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client(openclaw_client=_OpenClawAuthState())

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    assert resp.json()["openclaw_auth_state"] == "unauthorized"


def test_health_lite_marks_auth_unauthorized_from_runtime_route_401_detail(monkeypatch):
    """`health/lite` должен помечать unauthorized по route_detail c 401, даже без error_code."""

    class _OpenClawRoute401(_FakeOpenClaw):
        def get_last_runtime_route(self):
            return {
                "channel": "error",
                "provider": "google",
                "model": "google/gemini-2.5-flash",
                "status": "error",
                "error_code": None,
                "route_detail": "Provider returned HTTP 401 Unauthorized for current key",
            }

        def get_tier_state_export(self):
            return {
                "active_tier": "free",
                "last_error_code": None,
                "last_provider_status": "unknown",
                "last_recovery_action": "none",
            }

    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("OPENCLAW_TOKEN", "test-token")
    client = _make_client(openclaw_client=_OpenClawRoute401())

    resp = client.get("/api/health/lite")
    assert resp.status_code == 200
    assert resp.json()["openclaw_auth_state"] == "unauthorized"


def test_openclaw_cli_env_propagates_runtime_token(monkeypatch):
    """`openclaw` CLI env должен получать gateway token без подмены OPENCLAW_TOKEN."""
    monkeypatch.setenv("OPENCLAW_TOKEN", "token-from-runtime")
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: "gateway-token-from-config"),
    )

    env = WebApp._openclaw_cli_env()
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "gateway-token-from-config"
    assert env["OPENCLAW_TOKEN"] == "token-from-runtime"


def test_parse_openclaw_gateway_probe_extracts_reachability():
    """Парсер gateway probe должен извлекать reachable/detail/local target."""
    sample = """
Gateway Status
Reachable: yes
Probe budget: 3000ms

Targets
Local loopback ws://127.0.0.1:18789
  Connect: ok
""".strip()
    parsed = WebApp._parse_openclaw_gateway_probe(sample)
    assert parsed["gateway_reachable"] is True
    assert parsed["local_target"] == "ws://127.0.0.1:18789"
    assert "Connect: ok" in parsed["detail"]


def test_openclaw_cli_env_fallback_to_env_gateway_token(monkeypatch):
    """Если в конфиге нет токена, используем OPENCLAW_GATEWAY_TOKEN из env."""
    monkeypatch.setenv("OPENCLAW_GATEWAY_TOKEN", "gateway-token-from-env")
    monkeypatch.setattr(
        WebApp,
        "_openclaw_gateway_token_from_config",
        staticmethod(lambda: ""),
    )
    env = WebApp._openclaw_cli_env()
    assert env["OPENCLAW_GATEWAY_TOKEN"] == "gateway-token-from-env"
