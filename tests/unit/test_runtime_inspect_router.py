# -*- coding: utf-8 -*-
"""
Unit tests для runtime_inspect_router (Phase 2 Wave G, Session 25).

RouterContext-based extraction для /api/queue и /api/ctx.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.runtime_inspect_router import build_runtime_inspect_router


class _FakeQueueManager:
    def get_stats(self) -> dict:
        return {"active": 2, "pending": 1, "chats": {"-100123": {"depth": 1}}}


class _FakeAiRuntimeWithQueue:
    def __init__(self) -> None:
        self.queue_manager = _FakeQueueManager()


class _FakeAiRuntimeWithCtx:
    def get_context_snapshots(self) -> list[dict]:
        return [{"chat_id": 1, "messages": 3}, {"chat_id": 2, "messages": 5}]

    def get_context_snapshot(self, chat_id: int) -> dict:
        return {"chat_id": chat_id, "messages": 7, "last": "hello"}


class _FakeAiRuntimeNoQueueNoCtx:
    """ai_runtime без queue_manager и без get_context_snapshots."""

    pass


def _build_ctx(deps: dict | None = None) -> RouterContext:
    return RouterContext(
        deps=deps or {},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_runtime_inspect_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/queue
# ---------------------------------------------------------------------------


def test_queue_no_ai_runtime() -> None:
    """Без ai_runtime — ok=False, error=queue_not_configured."""
    resp = _client(_build_ctx()).get("/api/queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "queue_not_configured"


def test_queue_runtime_without_queue_manager() -> None:
    """ai_runtime без queue_manager — ok=False."""
    ctx = _build_ctx({"ai_runtime": _FakeAiRuntimeNoQueueNoCtx()})
    resp = _client(ctx).get("/api/queue")
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "queue_not_configured"


def test_queue_with_runtime() -> None:
    """С полноценным queue_manager — ok=True + stats."""
    ctx = _build_ctx({"ai_runtime": _FakeAiRuntimeWithQueue()})
    resp = _client(ctx).get("/api/queue")
    data = resp.json()
    assert data["ok"] is True
    assert data["queue"]["active"] == 2
    assert data["queue"]["pending"] == 1
    assert "-100123" in data["queue"]["chats"]


# ---------------------------------------------------------------------------
# /api/ctx
# ---------------------------------------------------------------------------


def test_ctx_no_ai_runtime() -> None:
    """Без ai_runtime — ok=False, error=ai_runtime_not_configured."""
    resp = _client(_build_ctx()).get("/api/ctx")
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "ai_runtime_not_configured"


def test_ctx_runtime_without_snapshots_method() -> None:
    """ai_runtime без get_context_snapshots — ctx_not_supported."""
    ctx = _build_ctx({"ai_runtime": _FakeAiRuntimeNoQueueNoCtx()})
    resp = _client(ctx).get("/api/ctx")
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "ctx_not_supported"


def test_ctx_all_chats() -> None:
    """Без chat_id — возвращает все contexts."""
    ctx = _build_ctx({"ai_runtime": _FakeAiRuntimeWithCtx()})
    resp = _client(ctx).get("/api/ctx")
    data = resp.json()
    assert data["ok"] is True
    assert len(data["contexts"]) == 2
    assert data["contexts"][0]["chat_id"] == 1


def test_ctx_specific_chat() -> None:
    """С chat_id — возвращает context конкретного чата."""
    ctx = _build_ctx({"ai_runtime": _FakeAiRuntimeWithCtx()})
    resp = _client(ctx).get("/api/ctx?chat_id=42")
    data = resp.json()
    assert data["ok"] is True
    assert data["context"]["chat_id"] == 42
    assert data["context"]["messages"] == 7
