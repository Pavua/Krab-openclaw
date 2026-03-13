# -*- coding: utf-8 -*-
"""
Тесты web endpoint'ов capability registry foundation.

Покрываем:
1) assistant capability refs на unified registry/policy;
2) policy matrix endpoint;
3) capability registry endpoint;
4) translator readiness refs на registry/matrix.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp
from tests.unit.test_web_app_runtime_endpoints import (
    _FakeOpenClaw,
    _FakeHealthClient,
    _FakeUserbot,
    _DummyRouter,
)


def _make_app(*, kraab_userbot=None) -> WebApp:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
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
        "kraab_userbot": kraab_userbot,
    }
    return WebApp(deps, port=18080, host="127.0.0.1")


def test_assistant_capabilities_expose_registry_and_policy_endpoints() -> None:
    """`/api/assistant/capabilities` должен указывать unified registry и policy matrix endpoint'ы."""
    client = TestClient(_make_app().app)

    resp = client.get("/api/assistant/capabilities")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "web_native"
    assert data["registry_endpoint"] == "/api/capabilities/registry"
    assert data["policy_matrix_endpoint"] == "/api/policy/matrix"


def test_policy_matrix_endpoint_returns_acl_policy_truth(monkeypatch) -> None:
    """`/api/policy/matrix` должен возвращать unified ACL/policy snapshot."""
    monkeypatch.setattr("src.modules.web_app.load_acl_runtime_state", lambda: {"owner": ["pablito"], "full": [], "partial": ["guest"]})
    client = TestClient(_make_app().app)

    resp = client.get("/api/policy/matrix")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    matrix = data["policy_matrix"]
    assert matrix["roles"]["owner"]["capabilities"]["acl_admin"] is True
    assert matrix["roles"]["partial"]["capabilities"]["file_ops"] is False
    assert matrix["summary"]["owner_subjects"] == 1


def test_capability_registry_endpoint_aggregates_contours(monkeypatch) -> None:
    """`/api/capabilities/registry` должен собирать unified capability registry."""
    monkeypatch.setattr("src.modules.web_app.load_acl_runtime_state", lambda: {"owner": ["pablito"], "full": [], "partial": []})
    client = TestClient(_make_app(kraab_userbot=_FakeUserbot()).app)

    resp = client.get("/api/capabilities/registry")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["contours"]["assistant"]["mode"] == "web_native"
    assert data["contours"]["telegram_userbot"]["primary_transport"] is True
    assert data["contours"]["translator"]["canonical_backend"] == "krab_voice_gateway"
    assert data["policy_matrix"]["roles"]["owner"]["subjects"] == ["pablito"]


def test_translator_readiness_exposes_registry_refs() -> None:
    """Translator readiness должен ссылаться на unified capability/policy endpoints."""
    client = TestClient(_make_app(kraab_userbot=_FakeUserbot()).app)

    resp = client.get("/api/translator/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["capability_registry_endpoint"] == "/api/capabilities/registry"
    assert data["policy_matrix_endpoint"] == "/api/policy/matrix"
