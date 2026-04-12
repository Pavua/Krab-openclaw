# -*- coding: utf-8 -*-
"""
Тесты policy/capability API endpoint'ов web-панели Krab.

Покрываем:
  GET /api/policy/matrix           — unified policy matrix (owner/full/partial/guest)
  GET /api/capabilities/registry   — единый capability registry
  GET /api/capabilities/system-control через registry["system_control"]
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """Создаёт WebApp с полным набором заглушек."""
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
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# /api/policy/matrix
# ---------------------------------------------------------------------------


def test_policy_matrix_ok() -> None:
    """GET /api/policy/matrix должен вернуть ok=True."""
    resp = _client().get("/api/policy/matrix")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_policy_matrix_has_policy_matrix_key() -> None:
    """Ответ содержит поле policy_matrix."""
    data = _client().get("/api/policy/matrix").json()
    assert "policy_matrix" in data


def test_policy_matrix_roles_present() -> None:
    """policy_matrix содержит все четыре роли ACL."""
    matrix = _client().get("/api/policy/matrix").json()["policy_matrix"]
    assert "roles" in matrix
    for role in ("owner", "full", "partial", "guest"):
        assert role in matrix["roles"], f"роль отсутствует: {role}"


def test_policy_matrix_role_order() -> None:
    """role_order перечислен в нужном порядке."""
    matrix = _client().get("/api/policy/matrix").json()["policy_matrix"]
    assert matrix["role_order"] == ["owner", "full", "partial", "guest"]


def test_policy_matrix_guardrails_present() -> None:
    """policy_matrix содержит секцию guardrails."""
    matrix = _client().get("/api/policy/matrix").json()["policy_matrix"]
    assert "guardrails" in matrix
    g = matrix["guardrails"]
    assert "web_write_requires_key" in g
    assert "partial_commands" in g


def test_policy_matrix_summary_counts() -> None:
    """Секция summary содержит числовые счётчики."""
    matrix = _client().get("/api/policy/matrix").json()["policy_matrix"]
    s = matrix["summary"]
    for field in ("owner_subjects", "full_subjects", "partial_subjects"):
        assert isinstance(s[field], int), f"поле {field} должно быть int"


def test_policy_matrix_collected_at_format() -> None:
    """collected_at имеет ISO-формат с timezone offset."""
    matrix = _client().get("/api/policy/matrix").json()["policy_matrix"]
    ts = matrix.get("collected_at", "")
    assert "T" in ts, "collected_at должен быть ISO datetime"


# ---------------------------------------------------------------------------
# /api/capabilities/registry
# ---------------------------------------------------------------------------


def test_capabilities_registry_ok() -> None:
    """GET /api/capabilities/registry должен вернуть ok=True."""
    resp = _client().get("/api/capabilities/registry")
    assert resp.status_code == 200
    assert resp.json().get("ok") is True


def test_capabilities_registry_has_system_contour() -> None:
    """Реестр содержит контур contours.system (system_control capabilities)."""
    data = _client().get("/api/capabilities/registry").json()
    assert "contours" in data
    assert "system" in data["contours"]


def test_capabilities_registry_system_control_browser_key() -> None:
    """contours.system.control содержит подкапабилити browser_control."""
    sc = _client().get("/api/capabilities/registry").json()["contours"]["system"]
    # system_control находится в sc["control"]["capabilities"]
    caps = sc.get("control", {}).get("capabilities", {})
    assert "browser_control" in caps


def test_capabilities_registry_system_control_statuses_are_strings() -> None:
    """Все статусы capabilities в contours.system.control — строки."""
    sc = _client().get("/api/capabilities/registry").json()["contours"]["system"]
    for key, val in sc.get("control", {}).get("capabilities", {}).items():
        status = val.get("status")
        assert isinstance(status, str), (
            f"capabilities[{key}].status должен быть str, got {status!r}"
        )


def test_capabilities_registry_policy_matrix_embedded() -> None:
    """Реестр содержит встроенный policy_matrix с ролями."""
    data = _client().get("/api/capabilities/registry").json()
    assert "policy_matrix" in data
    assert "roles" in data["policy_matrix"]


def test_capabilities_registry_no_exceptions_on_degraded_probes() -> None:
    """Реестр возвращает ok=True даже когда все live-пробы недоступны (нет bridge)."""
    # Патчим все bridge-модули, чтобы health_check бросал исключения
    patches = [
        patch(
            "src.integrations.browser_bridge.browser_bridge.health_check",
            side_effect=Exception("no cdp"),
        ),
        patch(
            "src.integrations.macos_automation.macos_automation.health_check",
            side_effect=Exception("no mac"),
        ),
    ]
    with patches[0], patches[1]:
        resp = _client().get("/api/capabilities/registry")
    # Даже при деградированных пробах эндпоинт не должен падать
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
