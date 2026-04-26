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

    def update_translator_runtime_profile(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if k != "persist":
                self._profile[k] = v

    def update_translator_session_state(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if k != "persist":
                self._session_state[k] = v


def _build_ctx(
    kraab: _FakeKraab | None = None,
    *,
    deps_extra: dict | None = None,
    runtime_lite_provider=None,
) -> RouterContext:
    deps = {"kraab_userbot": kraab or _FakeKraab()}
    if deps_extra:
        deps.update(deps_extra)
    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda h, t: None,
        runtime_lite_provider=runtime_lite_provider,
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


# ---------------------------------------------------------------------------
# Wave Q: readiness / control-plane / session-inspector / mobile-readiness /
# delivery-matrix
# ---------------------------------------------------------------------------


async def _fake_runtime_lite():
    return {"runtime_lite": True, "marker": "Q"}


def _wave_q_helpers() -> dict:
    """Build async snapshot helpers that return identifiable payloads."""
    captured: dict = {"calls": []}

    async def readiness(*, runtime_lite=None):
        captured["calls"].append(("readiness", runtime_lite))
        return {"ok": True, "kind": "readiness", "runtime_seen": runtime_lite}

    async def control_plane(*, runtime_lite=None):
        captured["calls"].append(("control_plane", runtime_lite))
        return {"ok": True, "kind": "control_plane"}

    async def session_inspector(*, runtime_lite=None, current_control_plane=None):
        captured["calls"].append(("inspector", current_control_plane))
        return {"ok": True, "kind": "inspector", "cp": current_control_plane}

    async def mobile_readiness(*, runtime_lite=None, current_control_plane=None):
        captured["calls"].append(("mobile", current_control_plane))
        return {"ok": True, "kind": "mobile"}

    async def delivery_matrix(
        *,
        runtime_lite=None,
        current_readiness=None,
        current_control_plane=None,
        current_mobile_readiness=None,
    ):
        captured["calls"].append(
            ("delivery", current_readiness, current_control_plane, current_mobile_readiness)
        )
        return {
            "ok": True,
            "kind": "delivery",
            "readiness": current_readiness,
            "mobile": current_mobile_readiness,
        }

    return {
        "_captured": captured,
        "translator_readiness_snapshot": readiness,
        "translator_control_plane_snapshot": control_plane,
        "translator_session_inspector_snapshot": session_inspector,
        "translator_mobile_readiness_snapshot": mobile_readiness,
        "translator_delivery_matrix_snapshot": delivery_matrix,
    }


def _ctx_with_q_helpers() -> tuple[RouterContext, dict]:
    helpers = _wave_q_helpers()
    captured = helpers.pop("_captured")
    ctx = _build_ctx(deps_extra=helpers, runtime_lite_provider=_fake_runtime_lite)
    return ctx, captured


def test_readiness_passes_runtime_lite_and_appends_endpoints() -> None:
    ctx, captured = _ctx_with_q_helpers()
    data = _client(ctx).get("/api/translator/readiness").json()
    assert data["ok"] is True
    assert data["kind"] == "readiness"
    # runtime_lite from provider пробрасывается в helper.
    assert data["runtime_seen"] == {"runtime_lite": True, "marker": "Q"}
    # endpoint добавляет 2 ссылки в snapshot.
    assert data["capability_registry_endpoint"] == "/api/capabilities/registry"
    assert data["policy_matrix_endpoint"] == "/api/policy/matrix"
    assert ("readiness", {"runtime_lite": True, "marker": "Q"}) in captured["calls"]


def test_readiness_helper_missing_returns_graceful_error() -> None:
    """Без injected helper → ok=False (не 500)."""
    ctx = _build_ctx(runtime_lite_provider=_fake_runtime_lite)
    data = _client(ctx).get("/api/translator/readiness").json()
    assert data["ok"] is False
    assert "translator_readiness_snapshot" in data["error"]


def test_control_plane_returns_helper_payload() -> None:
    ctx, captured = _ctx_with_q_helpers()
    data = _client(ctx).get("/api/translator/control-plane").json()
    assert data == {"ok": True, "kind": "control_plane"}
    assert any(c[0] == "control_plane" for c in captured["calls"])


def test_session_inspector_chains_control_plane() -> None:
    ctx, captured = _ctx_with_q_helpers()
    data = _client(ctx).get("/api/translator/session-inspector").json()
    assert data["kind"] == "inspector"
    # inspector получает control_plane payload.
    assert data["cp"]["kind"] == "control_plane"
    kinds = [c[0] for c in captured["calls"]]
    assert kinds.index("control_plane") < kinds.index("inspector")


def test_mobile_readiness_chains_control_plane() -> None:
    ctx, captured = _ctx_with_q_helpers()
    data = _client(ctx).get("/api/translator/mobile-readiness").json()
    assert data["kind"] == "mobile"
    kinds = [c[0] for c in captured["calls"]]
    assert "control_plane" in kinds
    assert "mobile" in kinds


def test_delivery_matrix_aggregates_chain() -> None:
    ctx, captured = _ctx_with_q_helpers()
    data = _client(ctx).get("/api/translator/delivery-matrix").json()
    assert data["kind"] == "delivery"
    assert data["readiness"]["kind"] == "readiness"
    assert data["mobile"]["kind"] == "mobile"
    kinds = [c[0] for c in captured["calls"]]
    # readiness + control_plane + mobile должны быть вызваны до delivery.
    assert kinds[-1] == "delivery"
    for required in ("readiness", "control_plane", "mobile"):
        assert required in kinds


# ---------------------------------------------------------------------------
# Wave S — POST endpoints
# ---------------------------------------------------------------------------


def test_session_toggle_starts_when_idle(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    kraab._session_state["session_status"] = "idle"
    ctx = _build_ctx(kraab)
    resp = _client(ctx).post("/api/translator/session/toggle", json={"chat_id": "42"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "started"
    assert data["status"] == "active"
    assert "42" in data["active_chats"]
    assert kraab._session_state["session_status"] == "active"


def test_session_toggle_stops_when_active(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    kraab._session_state["session_status"] = "active"
    ctx = _build_ctx(kraab)
    resp = _client(ctx).post("/api/translator/session/toggle", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "stopped"
    assert data["status"] == "idle"
    assert kraab._session_state["session_status"] == "idle"


def test_translator_auto_sets_auto_detect(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    ctx = _build_ctx(kraab)
    resp = _client(ctx).post("/api/translator/auto")
    assert resp.status_code == 200
    assert resp.json()["language_pair"] == "auto-detect"
    assert kraab._profile["language_pair"] == "auto-detect"


def test_translator_lang_valid_pair(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    ctx = _build_ctx(kraab)
    resp = _client(ctx).post("/api/translator/lang", json={"language_pair": "en-ru"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["language_pair"] == "en-ru"
    assert kraab._profile["language_pair"] == "en-ru"


def test_translator_lang_invalid_pair(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx = _build_ctx()
    resp = _client(ctx).post("/api/translator/lang", json={"language_pair": "zz-xx"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


def test_translator_translate_empty_text(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx = _build_ctx()
    resp = _client(ctx).post("/api/translator/translate", json={"text": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "text required" in data["error"]


def test_post_endpoints_require_write_access(monkeypatch) -> None:
    """С WEB_API_KEY=secret и без header → 403 на каждый POST."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    ctx = _build_ctx()
    client = _client(ctx)
    for path, body in [
        ("/api/translator/session/toggle", {}),
        ("/api/translator/auto", None),
        ("/api/translator/lang", {"language_pair": "en-ru"}),
        ("/api/translator/translate", {"text": "x"}),
    ]:
        if body is None:
            resp = client.post(path)
        else:
            resp = client.post(path, json=body)
        assert resp.status_code == 403, f"{path} should require write access"
