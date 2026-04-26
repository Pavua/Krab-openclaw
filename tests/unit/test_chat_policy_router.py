# -*- coding: utf-8 -*-
"""Тесты chat_policy_router (Smart Routing Phase 4, Session 26)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.chat_response_policy import ChatResponsePolicyStore
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.chat_policy_router import build_chat_policy_router


def _make_client(
    *,
    store: ChatResponsePolicyStore | None = None,
    write_raises: Exception | None = None,
) -> tuple[TestClient, ChatResponsePolicyStore]:
    deps: dict[str, Any] = {"chat_policy_store": store}

    def _assert_write(*a: Any, **kw: Any) -> None:
        if write_raises is not None:
            raise write_raises

    ctx = RouterContext(
        deps=deps,
        project_root=Path("."),
        web_api_key_fn=lambda: None,
        assert_write_access_fn=_assert_write,
    )
    app = FastAPI()
    app.include_router(build_chat_policy_router(ctx))
    return TestClient(app), store  # type: ignore[return-value]


@pytest.fixture
def store(tmp_path) -> ChatResponsePolicyStore:
    return ChatResponsePolicyStore(path=tmp_path / "policies.json")


def test_get_policy_default(store):
    client, _ = _make_client(store=store)
    r = client.get("/api/chat/policy/123")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["policy"]["chat_id"] == "123"
    assert body["policy"]["mode"] == "normal"
    assert "effective_threshold" in body["policy"]


def test_post_set_mode(store):
    client, _ = _make_client(store=store)
    r = client.post("/api/chat/policy/42", json={"mode": "cautious"})
    assert r.status_code == 200
    assert r.json()["policy"]["mode"] == "cautious"


def test_post_invalid_mode(store):
    client, _ = _make_client(store=store)
    r = client.post("/api/chat/policy/42", json={"mode": "wild"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_mode"


def test_post_threshold_in_range(store):
    client, _ = _make_client(store=store)
    r = client.post("/api/chat/policy/9", json={"threshold_override": 0.42})
    assert r.status_code == 200
    assert r.json()["policy"]["threshold_override"] == 0.42


def test_post_threshold_out_of_range(store):
    client, _ = _make_client(store=store)
    r = client.post("/api/chat/policy/9", json={"threshold_override": 1.5})
    assert r.status_code == 400
    assert r.json()["detail"] == "threshold_out_of_range"


def test_post_blocked_topics_must_be_list(store):
    client, _ = _make_client(store=store)
    r = client.post("/api/chat/policy/9", json={"blocked_topics": "x"})
    assert r.status_code == 400


def test_post_write_access_denied(store):
    from fastapi import HTTPException

    client, _ = _make_client(store=store, write_raises=HTTPException(status_code=403))
    r = client.post("/api/chat/policy/9", json={"mode": "normal"})
    assert r.status_code == 403


def test_list_policies_empty(store):
    client, _ = _make_client(store=store)
    r = client.get("/api/chat/policies")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["policies"] == []


def test_list_policies_filter_by_mode(store):
    store.update_policy("1", mode="silent")
    store.update_policy("2", mode="normal")
    store.update_policy("3", mode="silent")
    client, _ = _make_client(store=store)
    r = client.get("/api/chat/policies?mode=silent")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert {p["chat_id"] for p in body["policies"]} == {"1", "3"}


def test_delete_existing(store):
    store.update_policy("88", mode="cautious")
    client, _ = _make_client(store=store)
    r = client.delete("/api/chat/policy/88")
    assert r.status_code == 200
    body = r.json()
    assert body["existed"] is True
    assert body["chat_id"] == "88"


def test_delete_nonexistent(store):
    client, _ = _make_client(store=store)
    r = client.delete("/api/chat/policy/nope")
    assert r.status_code == 200
    assert r.json()["existed"] is False


def test_delete_write_access_denied(store):
    from fastapi import HTTPException

    client, _ = _make_client(store=store, write_raises=HTTPException(status_code=403))
    r = client.delete("/api/chat/policy/1")
    assert r.status_code == 403
