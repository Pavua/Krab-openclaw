# -*- coding: utf-8 -*-
"""
Тесты ``src.modules.web_routers.admin_router`` — Phase 2 Wave W (Session 25).

Покрытие сосредоточено на factory-pattern: build_admin_router(ctx) должен
работать stand-alone (без полного WebApp), используя helper'ы и сервисы
из ctx.deps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.admin_router import build_admin_router


# ---------------------------------------------------------------------------
# Test fixtures: fake provisioning_service + ACL helpers
# ---------------------------------------------------------------------------


class _FakeProvisioning:
    def __init__(self) -> None:
        self.applied: list[tuple[str, bool]] = []
        self.last_draft: dict[str, Any] | None = None

    def list_templates(self, entity: str) -> list[dict[str, Any]]:
        return [{"entity": entity, "name": "tpl1"}]

    def list_drafts(self, *, limit: int, status: str | None) -> list[dict[str, Any]]:
        return [{"id": "d1", "status": status or "draft", "limit": limit}]

    def create_draft(self, **kwargs: Any) -> dict[str, Any]:
        self.last_draft = kwargs
        return {"id": "draft_42", **kwargs}

    def preview_diff(self, draft_id: str) -> dict[str, Any]:
        if draft_id == "missing":
            raise KeyError("draft_not_found")
        return {"draft_id": draft_id, "diff": "+ new"}

    def apply_draft(self, draft_id: str, *, confirmed: bool) -> dict[str, Any]:
        self.applied.append((draft_id, confirmed))
        return {"draft_id": draft_id, "confirmed": confirmed, "applied": True}


def _make_client(
    *,
    provisioning: _FakeProvisioning | None = None,
    deps_overrides: dict[str, Any] | None = None,
    write_access_raises: Exception | None = None,
) -> TestClient:
    idem_state: dict[str, Any] = {}

    def _idem_get(ns: str, key: str) -> dict | None:
        if not key:
            return None
        entry = idem_state.get(f"{ns}:{key}")
        if not entry:
            return None
        data = dict(entry)
        data["idempotent_replay"] = True
        return data

    def _idem_set(ns: str, key: str, payload: dict) -> None:
        if not key:
            return
        idem_state[f"{ns}:{key}"] = dict(payload)

    deps: dict[str, Any] = {
        "provisioning_service": provisioning or _FakeProvisioning(),
        "black_box": None,
        "idempotency_get": _idem_get,
        "idempotency_set": _idem_set,
        "acl_load_state_helper": lambda: {"owner": ["o"], "subjects": []},
        "acl_owner_label_helper": lambda: "owner_user",
        "acl_owner_subjects_helper": lambda: ["owner_user"],
        "acl_update_subject_helper": lambda level, subject, add: {
            "level": level,
            "subject": subject,
            "changed": True,
            "path": "/tmp/acl.json",
            "state": {"after": True, "add": add},
        },
        "acl_partial_commands": {"!ask", "!search"},
        "acl_file_path": "/tmp/acl.json",
    }
    if deps_overrides:
        deps.update(deps_overrides)

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_access_raises is not None:
            raise write_access_raises

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )

    app = FastAPI()
    app.include_router(build_admin_router(ctx))
    client = TestClient(app)
    client._idem_state = idem_state  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# /api/provisioning/templates
# ---------------------------------------------------------------------------


def test_provisioning_templates_default_entity() -> None:
    client = _make_client()
    resp = client.get("/api/provisioning/templates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"] == "agent"
    assert body["templates"] == [{"entity": "agent", "name": "tpl1"}]


def test_provisioning_templates_503_when_service_missing() -> None:
    client = _make_client(deps_overrides={"provisioning_service": None})
    resp = client.get("/api/provisioning/templates")
    assert resp.status_code == 503
    assert "provisioning_service_not_configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/provisioning/drafts (GET + POST)
# ---------------------------------------------------------------------------


def test_provisioning_drafts_list_with_filter() -> None:
    client = _make_client()
    resp = client.get("/api/provisioning/drafts?status=open&limit=5")
    assert resp.status_code == 200
    drafts = resp.json()["drafts"]
    assert drafts == [{"id": "d1", "status": "open", "limit": 5}]


def test_provisioning_create_draft_happy_path() -> None:
    fake = _FakeProvisioning()
    client = _make_client(provisioning=fake)
    resp = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "Alice", "role": "qa"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["draft"]["id"] == "draft_42"
    assert fake.last_draft is not None
    assert fake.last_draft["name"] == "Alice"


def test_provisioning_create_draft_idempotent_replay() -> None:
    client = _make_client()
    headers = {"X-Idempotency-Key": "abc-123"}
    first = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "Bob"},
        headers=headers,
    )
    assert first.status_code == 200
    second = client.post(
        "/api/provisioning/drafts",
        json={"entity_type": "agent", "name": "Bob-changed"},
        headers=headers,
    )
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True


# ---------------------------------------------------------------------------
# /api/provisioning/preview/{draft_id} + /api/provisioning/apply/{draft_id}
# ---------------------------------------------------------------------------


def test_provisioning_preview_404_on_missing() -> None:
    client = _make_client()
    resp = client.get("/api/provisioning/preview/missing")
    assert resp.status_code == 404


def test_provisioning_apply_records_confirmation() -> None:
    fake = _FakeProvisioning()
    client = _make_client(provisioning=fake)
    resp = client.post("/api/provisioning/apply/draft_99?confirm=true")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["confirmed"] is True
    assert fake.applied == [("draft_99", True)]


# ---------------------------------------------------------------------------
# /api/userbot/acl/status + /api/userbot/acl/update
# ---------------------------------------------------------------------------


def test_userbot_acl_status_snapshot() -> None:
    client = _make_client()
    resp = client.get("/api/userbot/acl/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["acl"]["owner_username"] == "owner_user"
    assert body["acl"]["owner_subjects"] == ["owner_user"]
    assert body["acl"]["partial_commands"] == ["!ask", "!search"]
    assert body["acl"]["path"] == "/tmp/acl.json"


def test_userbot_acl_update_grant_happy_path() -> None:
    client = _make_client()
    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "grant", "level": "full", "subject": "@user42"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["acl"]["action"] == "grant"
    assert body["acl"]["level"] == "full"
    assert body["acl"]["subject"] == "@user42"
    assert body["acl"]["changed"] is True


def test_userbot_acl_update_invalid_action_400() -> None:
    client = _make_client()
    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "delete", "level": "full", "subject": "@u"},
    )
    assert resp.status_code == 400
    assert "acl_update_invalid_action" in resp.json()["detail"]


def test_userbot_acl_update_value_error_400() -> None:
    def _bad_update(level: str, subject: str, add: bool) -> dict[str, Any]:
        raise ValueError("bad_subject_format")

    client = _make_client(deps_overrides={"acl_update_subject_helper": _bad_update})
    resp = client.post(
        "/api/userbot/acl/update",
        json={"action": "grant", "level": "full", "subject": "??"},
    )
    assert resp.status_code == 400
    assert "bad_subject_format" in resp.json()["detail"]
