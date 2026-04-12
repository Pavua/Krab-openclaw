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
    _DummyRouter,
    _FakeHealthClient,
    _FakeOpenClaw,
    _FakeUserbot,
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
    monkeypatch.setattr(
        "src.modules.web_app.load_acl_runtime_state",
        lambda: {"owner": ["pablito"], "full": [], "partial": ["guest"]},
    )
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
    monkeypatch.setattr(
        "src.modules.web_app.load_acl_runtime_state",
        lambda: {"owner": ["pablito"], "full": [], "partial": []},
    )
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(
            lambda cls: {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "dmPolicy": "allowlist",
                        "groupPolicy": "allowlist",
                        "allowFrom": ["312322764"],
                        "groupAllowFrom": ["312322764"],
                    }
                }
            }
        ),
    )
    client = TestClient(_make_app(kraab_userbot=_FakeUserbot()).app)

    resp = client.get("/api/capabilities/registry")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["contours"]["assistant"]["mode"] == "web_native"
    assert data["contours"]["telegram_userbot"]["primary_transport"] is True
    assert data["contours"]["translator"]["canonical_backend"] == "krab_voice_gateway"
    assert data["contours"]["channels"]["summary"]["reserve_safe"] is True
    assert data["policy_matrix"]["roles"]["owner"]["subjects"] == ["pablito"]


def test_channel_capabilities_endpoint_returns_primary_and_reserve_truth(monkeypatch) -> None:
    """`/api/channels/capabilities` должен отдавать parity snapshot по primary/reserve каналам."""
    monkeypatch.setattr(
        WebApp,
        "_load_openclaw_runtime_config",
        classmethod(
            lambda cls: {
                "channels": {
                    "telegram": {
                        "enabled": True,
                        "dmPolicy": "allowlist",
                        "groupPolicy": "allowlist",
                        "allowFrom": ["312322764"],
                        "groupAllowFrom": ["312322764"],
                    },
                    "imessage": {"enabled": False},
                }
            }
        ),
    )
    client = TestClient(_make_app(kraab_userbot=_FakeUserbot()).app)

    resp = client.get("/api/channels/capabilities")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    snapshot = data["channel_capabilities"]
    assert snapshot["summary"]["primary_transport"] == "telegram_userbot"
    assert snapshot["summary"]["reserve_transport"] == "telegram_reserve_bot"
    assert snapshot["summary"]["reserve_safe"] is True
    assert snapshot["summary"]["shared_workspace_dir"].endswith("workspace-main-messaging")
    assert "shared_workspace" in snapshot
    assert snapshot["channels"][0]["semantics"]["streaming"] == "buffered_edit_loop"
    assert (
        snapshot["channels"][0]["semantics"]["reasoning_visibility"]
        == "owner_optional_separate_trace"
    )
    assert snapshot["channels"][1]["semantics"]["runtime_self_check"] == "not_confirmed"


def test_translator_readiness_exposes_registry_refs() -> None:
    """Translator readiness должен ссылаться на unified capability/policy endpoints."""
    client = TestClient(_make_app(kraab_userbot=_FakeUserbot()).app)

    resp = client.get("/api/translator/readiness")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["capability_registry_endpoint"] == "/api/capabilities/registry"
    assert data["policy_matrix_endpoint"] == "/api/policy/matrix"
