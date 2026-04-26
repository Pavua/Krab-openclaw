# -*- coding: utf-8 -*-
"""
Unit tests для translator_router (Phase 2 Wave K, Session 25).

Тестируют RouterContext-based extraction: создаём RouterContext напрямую
с fake kraab_userbot deps, без полного WebApp instance.

Endpoints:
- GET /api/translator/languages
- GET /api/translator/status
- GET /api/translator/history
- GET /api/translator/test
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.translator_router import build_translator_router


class _FakeKraab:
    """Минимальный userbot-stub с translator-методами."""

    def __init__(self) -> None:
        self._profile = {"language_pair": "es-ru", "enabled": True}
        self._session_state: dict = {
            "session_status": "idle",
            "active_chats": [],
            "stats": {"total_translations": 5, "total_latency_ms": 2500},
            "last_language_pair": "es-ru",
            "last_translated_original": "hola",
            "last_translated_translation": "привет",
        }

    def get_translator_runtime_profile(self) -> dict:
        return self._profile

    def get_translator_session_state(self) -> dict:
        return self._session_state


def _build_ctx(kraab: _FakeKraab | None = None) -> RouterContext:
    return RouterContext(
        deps={"kraab_userbot": kraab or _FakeKraab()},
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
    )


def _client(ctx: RouterContext) -> TestClient:
    app = FastAPI()
    app.include_router(build_translator_router(ctx))
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/translator/languages
# ---------------------------------------------------------------------------


def test_languages_ok_shape() -> None:
    resp = _client(_build_ctx()).get("/api/translator/languages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["current"] == "es-ru"
    assert isinstance(data["available"], list)
    assert "es-ru" in data["available"]
    # отсортированный список
    assert data["available"] == sorted(data["available"])


def test_languages_default_when_no_pair() -> None:
    """Если profile не содержит language_pair — используется es-ru."""
    kraab = _FakeKraab()
    kraab._profile = {}
    data = _client(_build_ctx(kraab=kraab)).get("/api/translator/languages").json()
    assert data["current"] == "es-ru"


# ---------------------------------------------------------------------------
# /api/translator/status
# ---------------------------------------------------------------------------


def test_status_ok() -> None:
    data = _client(_build_ctx()).get("/api/translator/status").json()
    assert data["ok"] is True
    assert data["profile"]["language_pair"] == "es-ru"
    assert "session" in data
    assert data["session"]["session_status"] == "idle"


def test_status_error_graceful() -> None:
    """Исключение в kraab → ok=False без 500."""
    kraab = _FakeKraab()
    kraab.get_translator_runtime_profile = lambda: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    resp = _client(_build_ctx(kraab=kraab)).get("/api/translator/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "boom" in body["error"]


# ---------------------------------------------------------------------------
# /api/translator/history
# ---------------------------------------------------------------------------


def test_history_ok_shape() -> None:
    data = _client(_build_ctx()).get("/api/translator/history").json()
    assert data["ok"] is True
    assert data["total_translations"] == 5
    assert data["avg_latency_ms"] == 500  # 2500/5
    assert data["last_pair"] == "es-ru"
    assert data["last_original"] == "hola"
    assert data["last_translation"] == "привет"
    assert data["history"] == []
    assert data["history_count"] == 0


def test_history_with_entries_reversed_and_clamped() -> None:
    """history reversed (новые первыми), n clamped 1..20."""
    kraab = _FakeKraab()
    kraab._session_state["history"] = [{"i": i} for i in range(25)]
    # n=3 → 3 последних, reversed
    data = _client(_build_ctx(kraab=kraab)).get("/api/translator/history?n=3").json()
    assert data["history_count"] == 25
    assert data["history"] == [{"i": 24}, {"i": 23}, {"i": 22}]
    # n=999 clamps to 20
    data2 = _client(_build_ctx(kraab=kraab)).get("/api/translator/history?n=999").json()
    assert len(data2["history"]) == 20


def test_history_zero_total_no_div_by_zero() -> None:
    kraab = _FakeKraab()
    kraab._session_state["stats"] = {"total_translations": 0, "total_latency_ms": 0}
    data = _client(_build_ctx(kraab=kraab)).get("/api/translator/history").json()
    assert data["ok"] is True
    assert data["total_translations"] == 0
    assert data["avg_latency_ms"] == 0


# ---------------------------------------------------------------------------
# /api/translator/test
# ---------------------------------------------------------------------------


def test_test_no_text_returns_error() -> None:
    """Без ?text= → ok=False и понятная ошибка."""
    data = _client(_build_ctx()).get("/api/translator/test").json()
    assert data["ok"] is False
    assert "text" in data["error"].lower()
