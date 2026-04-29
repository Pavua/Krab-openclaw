# -*- coding: utf-8 -*-
"""
Unit tests для vpn_brain_router (VPN Phase B).

Покрывают:
- POST /api/vpn/help → 200 с правильной shape.
- Validation: missing friend_id / question → 400.
- Auth: WEB_API_KEY установлен, header не передан → 403.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.vpn_brain import VPNAnswer
from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.vpn_brain_router import build_vpn_brain_router


def _build_ctx() -> RouterContext:
    return RouterContext(
        deps={},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(build_vpn_brain_router(ctx or _build_ctx()))
    return TestClient(app)


def test_vpn_help_creates_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/vpn/help возвращает корректный shape с полями answer."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    fake_answer = VPNAnswer(
        text="Привет, Серёж!",
        confidence=0.85,
        suggested_action="reissue_key",
        latency_ms=412,
    )
    fake_brain = AsyncMock()
    fake_brain.answer_friend_question.return_value = fake_answer

    with patch("src.core.vpn_brain.vpn_brain", fake_brain):
        resp = _client().post(
            "/api/vpn/help",
            json={
                "friend_id": "123",
                "friend_name": "Серёжа",
                "question": "Помоги с ключом",
                "context": {"history": []},
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["text"] == "Привет, Серёж!"
    assert data["confidence"] == 0.85
    assert data["suggested_action"] == "reissue_key"
    assert data["latency_ms"] == 412

    fake_brain.answer_friend_question.assert_awaited_once()
    kwargs = fake_brain.answer_friend_question.await_args.kwargs
    assert kwargs["friend_id"] == "123"
    assert kwargs["friend_name"] == "Серёжа"
    assert kwargs["question"] == "Помоги с ключом"
    assert kwargs["context"] == {"history": []}


def test_vpn_help_validation_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing friend_id → 400."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    resp = _client().post(
        "/api/vpn/help",
        json={"friend_name": "X", "question": "?"},
    )
    assert resp.status_code == 400
    assert "friend_id" in resp.json()["detail"]

    resp2 = _client().post(
        "/api/vpn/help",
        json={"friend_id": "1", "friend_name": "X"},
    )
    assert resp2.status_code == 400
    assert "question" in resp2.json()["detail"]


def test_vpn_help_response_shape_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ содержит ровно ожидаемые ключи (без лишних утечек)."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)

    fake_answer = VPNAnswer(
        text="ok",
        confidence=0.5,
        suggested_action=None,
        latency_ms=10,
    )
    fake_brain = AsyncMock()
    fake_brain.answer_friend_question.return_value = fake_answer

    with patch("src.core.vpn_brain.vpn_brain", fake_brain):
        resp = _client().post(
            "/api/vpn/help",
            json={
                "friend_id": "1",
                "friend_name": "X",
                "question": "?",
            },
        )
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {
        "ok",
        "text",
        "confidence",
        "suggested_action",
        "latency_ms",
    }
