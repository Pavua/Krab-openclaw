# -*- coding: utf-8 -*-
"""
Unit tests для model_router (Phase 2 Wave FF, Session 25).

Покрывают:
- GET  /api/model/status
- POST /api/model/switch (200 + 403 + missing model)
- GET  /api/model/recommend
- POST /api/model/preflight (+ missing prompt 400)
- GET  /api/model/explain
- GET  /api/model/feedback
- POST /api/model/feedback (+ idempotency)
- GET  /api/model/local/status (через resolve_local_runtime_truth_helper)

RouterContext создаётся напрямую — router self-contained, WebApp не нужен.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.model_router import build_model_router


class _FakeRouter:
    force_mode = "auto"
    routing_policy = "balanced"
    cloud_soft_cap_reached = False
    is_local_available = True

    def get_profile_recommendation(self, profile: str = "chat") -> dict:
        return {"profile": profile, "model": "google/gemini-test", "channel": "cloud"}

    def get_task_preflight(self, *, prompt: str, task_type: str, **_kwargs) -> dict:
        return {"prompt": prompt, "task_type": task_type, "channel": "cloud"}

    def get_route_explain(self, *, prompt: str, task_type: str, **_kwargs) -> dict:
        return {"reason": {"code": "ok"}, "prompt": prompt, "task_type": task_type}

    def get_feedback_summary(self, *, profile, top: int) -> dict:
        return {"profile": profile, "top": top, "items": []}

    def submit_feedback(self, *, score: int, profile, model_name, channel, note) -> dict:
        return {
            "score": score,
            "profile": profile,
            "model": model_name,
            "channel": channel,
            "note": note,
        }


class _FakeMM:
    active_model_id = "google/gemini-test"

    def format_status(self) -> str:
        return "google/gemini-test (ok)"

    def set_provider(self, provider: str) -> None:
        self.active_model_id = f"provider:{provider}"

    def set_model(self, model: str) -> None:
        self.active_model_id = model


class _FakeOC:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "model": "google/gemini-test", "status": "ok"}


def _build_ctx(*, idem_store: dict | None = None, local_truth: dict | None = None) -> RouterContext:
    deps: dict = {"router": _FakeRouter()}
    store = idem_store if idem_store is not None else {}
    deps["idempotency_get"] = lambda kind, key: store.get((kind, key)) if key else None
    deps["idempotency_set"] = lambda kind, key, payload: (
        store.__setitem__((kind, key), payload) if key else None
    )
    if local_truth is not None:
        deps["resolve_local_runtime_truth_helper"] = lambda router: local_truth
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_model_router(ctx or _build_ctx()))
    return TestClient(app)


# ---------- GET /api/model/status ------------------------------------------


def test_model_status_returns_route_and_active_model() -> None:
    with (
        patch("src.model_manager.model_manager", _FakeMM()),
        patch("src.openclaw_client.openclaw_client", _FakeOC()),
    ):
        resp = _client().get("/api/model/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["route"]["model"] == "google/gemini-test"
    assert data["active_model"] == "google/gemini-test"


# ---------- POST /api/model/switch -----------------------------------------


def test_model_switch_missing_model_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post("/api/model/switch", json={})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_model_switch_auto_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mm = _FakeMM()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _client().post("/api/model/switch", json={"model": "auto"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"] == "auto"


def test_model_switch_explicit_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    fake_mm = _FakeMM()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _client().post("/api/model/switch", json={"model": "google/gemini-3-pro-preview"})
    assert resp.status_code == 200
    assert resp.json()["active"] == "google/gemini-3-pro-preview"


def test_model_switch_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post("/api/model/switch", json={"model": "auto"})
    assert resp.status_code == 403


# ---------- GET /api/model/recommend ---------------------------------------


def test_model_recommend_default_profile() -> None:
    resp = _client().get("/api/model/recommend")
    assert resp.status_code == 200
    assert resp.json()["profile"] == "chat"


def test_model_recommend_custom_profile() -> None:
    resp = _client().get("/api/model/recommend?profile=code")
    assert resp.json()["profile"] == "code"


# ---------- POST /api/model/preflight --------------------------------------


def test_model_preflight_missing_prompt_returns_400() -> None:
    resp = _client().post("/api/model/preflight", json={})
    assert resp.status_code == 400


def test_model_preflight_returns_plan() -> None:
    resp = _client().post(
        "/api/model/preflight", json={"prompt": "Hello world", "task_type": "code"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["preflight"]["prompt"] == "Hello world"
    assert body["preflight"]["task_type"] == "code"


# ---------- GET /api/model/explain -----------------------------------------


def test_model_explain_uses_route_explain_when_available() -> None:
    resp = _client().get("/api/model/explain?prompt=hi&task_type=chat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["explain"]["prompt"] == "hi"


# ---------- GET /api/model/feedback ----------------------------------------


def test_model_feedback_summary_returns_items() -> None:
    resp = _client().get("/api/model/feedback?profile=chat&top=3")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["feedback"]["top"] == 3


# ---------- POST /api/model/feedback ---------------------------------------


def test_model_feedback_submit_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post(
        "/api/model/feedback",
        json={"score": 5, "profile": "chat", "model": "google/gemini-test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["result"]["score"] == 5


def test_model_feedback_submit_idempotency_returns_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    store: dict = {}
    ctx = _build_ctx(idem_store=store)
    client = _client(ctx)
    headers = {"X-Idempotency-Key": "abc-123"}
    r1 = client.post("/api/model/feedback", json={"score": 4}, headers=headers)
    assert r1.status_code == 200
    # Cached returns same payload — even if router would error, returns first body.
    r2 = client.post("/api/model/feedback", json={"score": 1}, headers=headers)
    assert r2.status_code == 200
    assert r2.json() == r1.json()


def test_model_feedback_submit_invalid_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    resp = _client().post("/api/model/feedback", json={"score": 5})
    assert resp.status_code == 403


# ---------- GET /api/model/local/status ------------------------------------


def test_model_local_status_loaded_lifecycle() -> None:
    truth = {
        "active_model": "local-llama",
        "engine": "lmstudio",
        "runtime_url": "http://localhost:1234",
        "is_loaded": True,
        "runtime_reachable": True,
        "loaded_models": ["local-llama"],
        "probe_state": "ok",
        "error": "",
    }
    ctx = _build_ctx(local_truth=truth)
    resp = _client(ctx).get("/api/model/local/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "loaded"
    assert body["model_name"] == "local-llama"
    assert body["details"]["is_loaded"] is True


def test_model_local_status_not_loaded_lifecycle() -> None:
    truth = {
        "active_model": "",
        "engine": "lmstudio",
        "runtime_url": "n/a",
        "is_loaded": False,
        "runtime_reachable": False,
        "loaded_models": [],
        "probe_state": "down",
        "error": "",
    }
    ctx = _build_ctx(local_truth=truth)
    resp = _client(ctx).get("/api/model/local/status")
    body = resp.json()
    assert body["status"] == "not_loaded"
