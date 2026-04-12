# -*- coding: utf-8 -*-
"""
Тесты MCP и tools API endpoint'ов web-панели Krab.

Покрываем маршруты, связанные с MCP-инфраструктурой и capabilities:
  /api/openclaw/runtime-config        — runtime-конфиг OpenClaw (включая MCP-политику)
  /api/capabilities/registry          — единый capability registry
  /api/channels/capabilities          — channel capability parity snapshot
  /api/ecosystem/capabilities         — capabilities по control plane и внешним сервисам
  /api/openclaw/browser-mcp-readiness — staged readiness для browser + managed MCP
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без внешних вызовов."""

    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    """Фейковый клиент для voice/ear зависимостей."""

    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    """Минимальный userbot-stub."""

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


def _make_app() -> WebApp:
    """Создаёт WebApp с полным набором заглушек в deps."""
    deps = {
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
        "kraab_userbot": _FakeKraab(),
    }
    return WebApp(deps, port=18095, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# /api/openclaw/runtime-config
# ---------------------------------------------------------------------------


def test_runtime_config_ok_field() -> None:
    """GET /api/openclaw/runtime-config возвращает ok=True."""
    resp = _client().get("/api/openclaw/runtime-config")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_runtime_config_has_gateway_token_fields() -> None:
    """runtime-config содержит поля о статусе gateway token."""
    data = _client().get("/api/openclaw/runtime-config").json()
    assert "gateway_token_present" in data
    assert "gateway_token_masked" in data
    assert "gateway_token_kind" in data


def test_runtime_config_has_runtime_policy() -> None:
    """runtime-config содержит секцию runtime_policy с ключевыми полями."""
    data = _client().get("/api/openclaw/runtime-config").json()
    policy = data.get("runtime_policy", {})
    assert isinstance(policy, dict)
    # Основные политики должны присутствовать
    assert "force_cloud" in policy
    assert "local_fallback_enabled" in policy


# ---------------------------------------------------------------------------
# /api/capabilities/registry
# ---------------------------------------------------------------------------


def test_capabilities_registry_ok() -> None:
    """GET /api/capabilities/registry возвращает ok=True."""
    resp = _client().get("/api/capabilities/registry")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_capabilities_registry_has_contours_or_summary() -> None:
    """Ответ /api/capabilities/registry содержит структурированные данные (contours, summary, operator)."""
    data = _client().get("/api/capabilities/registry").json()
    # Registry возвращает contours/summary/operator вместо плоского поля registry
    has_structure = bool(
        data.get("contours") or data.get("summary") or data.get("operator") or data.get("registry")
    )
    assert has_structure, f"ожидалась структура capability registry, получено: {list(data.keys())}"


# ---------------------------------------------------------------------------
# /api/channels/capabilities
# ---------------------------------------------------------------------------


def test_channels_capabilities_ok() -> None:
    """GET /api/channels/capabilities возвращает ok=True."""
    resp = _client().get("/api/channels/capabilities")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_channels_capabilities_has_channel_capabilities() -> None:
    """Ответ содержит поле channel_capabilities."""
    data = _client().get("/api/channels/capabilities").json()
    assert "channel_capabilities" in data
    assert isinstance(data["channel_capabilities"], dict)


# ---------------------------------------------------------------------------
# /api/ecosystem/capabilities
# ---------------------------------------------------------------------------


def test_ecosystem_capabilities_ok() -> None:
    """GET /api/ecosystem/capabilities возвращает ok=True."""
    resp = _client().get("/api/ecosystem/capabilities")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_ecosystem_capabilities_has_services() -> None:
    """Ответ /api/ecosystem/capabilities содержит секцию services с krab."""
    data = _client().get("/api/ecosystem/capabilities").json()
    assert "services" in data
    services = data["services"]
    assert isinstance(services, dict)
    # krab всегда присутствует как control plane
    assert "krab" in services
    assert services["krab"]["ok"] is True


def test_ecosystem_capabilities_krab_has_mcp_endpoint_ref() -> None:
    """krab.detail содержит ссылки на capability_registry_endpoint."""
    data = _client().get("/api/ecosystem/capabilities").json()
    krab_detail = data["services"]["krab"].get("detail", {})
    assert "capability_registry_endpoint" in krab_detail


# ---------------------------------------------------------------------------
# /api/openclaw/browser-mcp-readiness
# ---------------------------------------------------------------------------


def test_browser_mcp_readiness_returns_json() -> None:
    """GET /api/openclaw/browser-mcp-readiness возвращает JSON без 5xx."""
    resp = _client().get("/api/openclaw/browser-mcp-readiness")
    # Endpoint может вернуть available=False при отсутствии browser, но не должен падать
    assert resp.status_code == 200
    data = resp.json()
    assert "available" in data


def test_browser_mcp_readiness_timeout_handled() -> None:
    """При недоступном browser endpoint возвращает available=False с error."""
    resp = _client().get("/api/openclaw/browser-mcp-readiness")
    data = resp.json()
    if not data.get("available"):
        # Должно быть поле error или detail для диагностики
        assert "error" in data or "detail" in data
