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


# ---------------------------------------------------------------------------
# Wave HH — translator session POST endpoints через helper injection
# ---------------------------------------------------------------------------


class _FakeVoiceGateway:
    """Стаб Voice Gateway client для session POST endpoints."""

    def __init__(self, *, ok: bool = True, error: str = "") -> None:
        self.ok = ok
        self.error = error
        self.calls: list[tuple[str, tuple, dict]] = []

    def _result(self, **extra) -> dict:
        if not self.ok:
            return {"ok": False, "error": self.error or "gateway_failed"}
        out = {"ok": True, "session_id": "sess-1", "result": {}}
        out.update(extra)
        return out

    async def start_session(self, **kwargs):
        self.calls.append(("start_session", (), kwargs))
        return self._result()

    async def patch_session(self, session_id, **kwargs):
        self.calls.append(("patch_session", (session_id,), kwargs))
        return self._result(session_id=session_id)

    async def stop_session(self, session_id):
        self.calls.append(("stop_session", (session_id,), {}))
        return self._result(session_id=session_id)

    async def tune_runtime(self, session_id, **kwargs):
        self.calls.append(("tune_runtime", (session_id,), kwargs))
        return self._result(session_id=session_id)

    async def send_quick_phrase(self, session_id, **kwargs):
        self.calls.append(("send_quick_phrase", (session_id,), kwargs))
        return self._result(session_id=session_id)

    async def build_summary(self, session_id, **kwargs):
        self.calls.append(("build_summary", (session_id,), kwargs))
        return self._result(session_id=session_id)


def _build_wave_hh_ctx(
    gateway: _FakeVoiceGateway | None = None,
    *,
    resolve_raises: bool = False,
    action_response_payload: dict | None = None,
) -> tuple[RouterContext, dict]:
    """RouterContext с инжектированными wave-HH helpers."""
    captured: dict = {"vg_start_calls": [], "vg_stop_calls": [], "action_calls": []}
    gw = gateway or _FakeVoiceGateway()

    def _gw_helper():
        return gw

    async def _resolve(*, requested_session_id: str = ""):
        if resolve_raises:
            from fastapi import HTTPException as _HE

            raise _HE(status_code=400, detail="translator_session_required")
        sid = requested_session_id or "sess-current"
        return sid, {"runtime": "lite"}, {"operator_actions": {"draft_defaults": {}}}

    async def _action_response(*, action, gateway_result, runtime_lite=None):
        captured["action_calls"].append((action, gateway_result, runtime_lite))
        return action_response_payload or {
            "ok": True,
            "action": action,
            "session_id": str(gateway_result.get("session_id") or ""),
        }

    def _err_detail(result, *, fallback):
        return 503, str(result.get("error") or fallback)

    async def _vg_start(session_id, voice_gateway):
        captured["vg_start_calls"].append((session_id, voice_gateway))

    async def _vg_stop():
        captured["vg_stop_calls"].append(True)

    deps_extra = {
        "translator_gateway_client_helper": _gw_helper,
        "translator_resolve_session_context_helper": _resolve,
        "translator_action_response_helper": _action_response,
        "translator_gateway_error_detail_helper": _err_detail,
        "vg_subscriber_start_helper": _vg_start,
        "vg_subscriber_stop_helper": _vg_stop,
    }
    return _build_ctx(deps_extra=deps_extra), captured


def test_session_start_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, cap = _build_wave_hh_ctx()
    resp = _client(ctx).post(
        "/api/translator/session/start",
        json={"label": "demo", "src_lang": "es", "tgt_lang": "ru"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "start_session"
    # vg subscriber запущен после успешного старта
    assert cap["vg_start_calls"], "vg_subscriber_start_helper must be invoked"
    assert cap["vg_start_calls"][0][0] == "sess-1"


def test_session_start_gateway_failure_raises_503(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway(ok=False, error="gateway_down")
    ctx, _cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post("/api/translator/session/start", json={})
    assert resp.status_code == 503
    assert "gateway_down" in resp.json()["detail"]


def test_session_policy_requires_patch(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post("/api/translator/session/policy", json={"session_id": "sess-x"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "translator_session_policy_patch_required"


def test_session_policy_applies_patch(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/session/policy",
        json={"session_id": "sid-9", "translation_mode": "auto_to_ru"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "update_session_policy"
    # patch_session должен быть вызван с переданным session_id
    method, args, kwargs = gw.calls[-1]
    assert method == "patch_session"
    assert args == ("sid-9",)
    assert kwargs.get("translation_mode") == "auto_to_ru"


def test_session_action_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post("/api/translator/session/action", json={"action": "fly"})
    assert resp.status_code == 400


def test_session_action_stop_calls_vg_stop(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/session/action", json={"action": "stop", "session_id": "sid-3"}
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "stop_session"
    assert cap["vg_stop_calls"], "vg_subscriber_stop_helper must be invoked on stop"
    assert gw.calls[-1][0] == "stop_session"


def test_session_action_pause_uses_patch(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, _cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/session/action", json={"action": "pause"}
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "pause_session"
    method, _args, kwargs = gw.calls[-1]
    assert method == "patch_session"
    assert kwargs.get("status") == "paused"


def test_session_runtime_tune_requires_some_field(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post("/api/translator/session/runtime-tune", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "translator_runtime_tune_patch_required"


def test_session_runtime_tune_invalid_latency(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post(
        "/api/translator/session/runtime-tune", json={"target_latency_ms": "not-int"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "translator_target_latency_invalid"


def test_session_runtime_tune_happy(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, _cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/session/runtime-tune",
        json={"target_latency_ms": 250, "vad_sensitivity": 0.6, "buffering_mode": "low"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "runtime_tune_session"
    method, _args, kwargs = gw.calls[-1]
    assert method == "tune_runtime"
    assert kwargs == {
        "buffering_mode": "low",
        "target_latency_ms": 250,
        "vad_sensitivity": 0.6,
    }


def test_session_quick_phrase_requires_text(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post("/api/translator/session/quick-phrase", json={"text": ""})
    assert resp.status_code == 400


def test_session_quick_phrase_uses_defaults(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, _cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/session/quick-phrase",
        json={"text": "Hola", "target_lang": "en"},
    )
    assert resp.status_code == 200
    method, _args, kwargs = gw.calls[-1]
    assert method == "send_quick_phrase"
    assert kwargs.get("text") == "Hola"
    assert kwargs.get("target_lang") == "en"
    # дефолты применяются для отсутствующих полей
    assert kwargs.get("source_lang") == "ru"


def test_session_summary_invalid_max_items(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_hh_ctx()
    resp = _client(ctx).post(
        "/api/translator/session/summary", json={"max_items": "abc"}
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "translator_summary_max_items_invalid"


def test_session_summary_happy(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeVoiceGateway()
    ctx, _cap = _build_wave_hh_ctx(gateway=gw)
    resp = _client(ctx).post("/api/translator/session/summary", json={"max_items": 5})
    assert resp.status_code == 200
    assert resp.json()["action"] == "build_session_summary"
    method, _args, kwargs = gw.calls[-1]
    assert method == "build_summary"
    assert kwargs == {"max_items": 5}


def test_session_post_endpoints_require_write_access(monkeypatch) -> None:
    """С WEB_API_KEY=secret и без header → 403 на каждый wave-HH POST."""
    monkeypatch.setenv("WEB_API_KEY", "secret")
    ctx, _cap = _build_wave_hh_ctx()
    client = _client(ctx)
    for path in (
        "/api/translator/session/start",
        "/api/translator/session/policy",
        "/api/translator/session/action",
        "/api/translator/session/runtime-tune",
        "/api/translator/session/quick-phrase",
        "/api/translator/session/summary",
    ):
        resp = client.post(path, json={})
        assert resp.status_code == 403, f"{path} should require write access"


# ---------------------------------------------------------------------------
# Wave II — translator/mobile POST endpoints + session/escalate
# ---------------------------------------------------------------------------


class _FakeMobileGateway(_FakeVoiceGateway):
    """Расширяет _FakeVoiceGateway mobile-методами."""

    async def register_mobile_device(self, **kwargs):
        self.calls.append(("register_mobile_device", (), kwargs))
        return self._result(device_id=kwargs.get("device_id"))

    async def bind_mobile_device(self, device_id, **kwargs):
        self.calls.append(("bind_mobile_device", (device_id,), kwargs))
        return self._result(device_id=device_id)

    async def delete_mobile_device(self, device_id):
        self.calls.append(("delete_mobile_device", (device_id,), {}))
        return self._result(device_id=device_id)


def _build_wave_ii_ctx(
    *,
    gateway: _FakeMobileGateway | None = None,
    inbox_upsert_ok: bool = True,
    inspector_payload: dict | None = None,
) -> tuple[RouterContext, dict]:
    """RouterContext с инжектированными wave-II helpers (mobile + escalate)."""
    captured: dict = {
        "mobile_action_calls": [],
        "inbox_upsert_calls": [],
    }
    gw = gateway or _FakeMobileGateway()

    def _gw_helper():
        return gw

    async def _resolve(*, requested_session_id: str = ""):
        sid = requested_session_id or "sess-current"
        return sid, {"runtime": "lite"}, {"sessions": {"current_session_id": sid}}

    async def _mobile_action_response(
        *, action, gateway_result, runtime_lite=None, current_control_plane=None
    ):
        captured["mobile_action_calls"].append(
            (action, dict(gateway_result), runtime_lite, current_control_plane)
        )
        return {
            "ok": True,
            "action": action,
            "gateway_result": gateway_result,
        }

    def _mobile_err_detail(result, *, fallback):
        return 503, str(result.get("error") or fallback)

    async def _control_plane_snapshot(*, runtime_lite=None):
        return {"sessions": {"current_session_id": "sess-current"}}

    async def _mobile_readiness_snapshot(*, runtime_lite=None, current_control_plane=None):
        return {
            "devices": {
                "selected_device_id": "iphone-default",
                "items": [],
            }
        }

    async def _session_inspector(*, runtime_lite=None, current_control_plane=None):
        return inspector_payload or {
            "session_status": "active",
            "gateway_status": "ok",
            "why_report": {"items": ["latency-spike"]},
            "timeline": {"stats": {"events": 3}},
            "escalation": {
                "suggested_title": "Translator session diagnostics",
                "suggested_body": "Latency spike detected, please review.",
            },
        }

    async def _readiness(*, runtime_lite=None):
        return {"ok": True, "ready": True}

    def _inbox_upsert(**kwargs):
        captured["inbox_upsert_calls"].append(kwargs)
        if not inbox_upsert_ok:
            return {"ok": False, "error": "inbox_upsert_failed"}
        return {
            "ok": True,
            "item": {
                "kind": "owner_task",
                "source": kwargs.get("source"),
                "title": kwargs.get("title"),
            },
        }

    def _inbox_summary():
        return {"pending_owner_tasks": 1}

    async def _runtime_lite_provider():
        return {"runtime": "lite"}

    deps_extra = {
        "translator_gateway_client_helper": _gw_helper,
        "translator_resolve_session_context_helper": _resolve,
        "translator_mobile_action_response_helper": _mobile_action_response,
        "translator_mobile_gateway_error_detail_helper": _mobile_err_detail,
        "translator_control_plane_snapshot": _control_plane_snapshot,
        "translator_mobile_readiness_snapshot": _mobile_readiness_snapshot,
        "translator_session_inspector_snapshot": _session_inspector,
        "translator_readiness_snapshot": _readiness,
        "inbox_service_upsert_owner_task_helper": _inbox_upsert,
        "inbox_service_get_summary_helper": _inbox_summary,
    }
    ctx = _build_ctx(deps_extra=deps_extra, runtime_lite_provider=_runtime_lite_provider)
    return ctx, captured


def test_mobile_register_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeMobileGateway()
    ctx, cap = _build_wave_ii_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/mobile/register",
        json={"device_id": "iphone-1", "voip_push_token": "tok"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "register_mobile_device"
    method, _args, kwargs = gw.calls[-1]
    assert method == "register_mobile_device"
    assert kwargs.get("device_id") == "iphone-1"


def test_mobile_register_gateway_error_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeMobileGateway(ok=False, error="apns_unauthorized")
    ctx, _cap = _build_wave_ii_ctx(gateway=gw)
    resp = _client(ctx).post("/api/translator/mobile/register", json={})
    assert resp.status_code == 503
    assert "apns_unauthorized" in resp.json()["detail"]


def test_mobile_bind_requires_device_id(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_ii_ctx()
    resp = _client(ctx).post("/api/translator/mobile/bind", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "device_id_required"


def test_mobile_bind_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeMobileGateway()
    ctx, cap = _build_wave_ii_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/mobile/bind",
        json={"device_id": "IPHONE-2", "session_id": "sess-9"},
    )
    assert resp.status_code == 200
    assert resp.json()["action"] == "bind_mobile_device"
    method, args, kwargs = gw.calls[-1]
    assert method == "bind_mobile_device"
    # device_id нормализуется в lower
    assert args == ("iphone-2",)
    assert kwargs.get("session_id") == "sess-9"


def test_mobile_remove_uses_selected_device_when_body_empty(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeMobileGateway()
    ctx, _cap = _build_wave_ii_ctx(gateway=gw)
    resp = _client(ctx).post("/api/translator/mobile/remove", json={})
    assert resp.status_code == 200
    assert resp.json()["action"] == "remove_mobile_device"
    method, args, _kwargs = gw.calls[-1]
    assert method == "delete_mobile_device"
    # fallback на selected_device_id из mobile_readiness
    assert args == ("iphone-default",)


def test_mobile_remove_gateway_error_returns_503(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    gw = _FakeMobileGateway(ok=False, error="device_not_found")
    ctx, _cap = _build_wave_ii_ctx(gateway=gw)
    resp = _client(ctx).post(
        "/api/translator/mobile/remove", json={"device_id": "iphone-x"}
    )
    assert resp.status_code == 503
    assert "device_not_found" in resp.json()["detail"]


def test_session_escalate_requires_title_or_body(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    # inspector без escalation suggestions → 400 при отсутствии title/body в body
    ctx, _cap = _build_wave_ii_ctx(
        inspector_payload={
            "session_status": "active",
            "gateway_status": "ok",
            "why_report": {"items": []},
            "timeline": {"stats": {}},
            "escalation": {},
        }
    )
    resp = _client(ctx).post("/api/translator/session/escalate", json={})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "translator_session_escalation_title_body_required"


def test_session_escalate_happy_path(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, cap = _build_wave_ii_ctx()
    resp = _client(ctx).post("/api/translator/session/escalate", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["action"] == "escalate_session"
    assert data["inbox_result"]["source"] == "translator-ui"
    assert data["inbox_summary"]["pending_owner_tasks"] == 1
    # severity warning из-за непустых why_items
    upsert_call = cap["inbox_upsert_calls"][-1]
    assert upsert_call["severity"] == "warning"
    assert upsert_call["team_id"] == "translator"


def test_session_escalate_inbox_failure_returns_500(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "")
    ctx, _cap = _build_wave_ii_ctx(inbox_upsert_ok=False)
    resp = _client(ctx).post("/api/translator/session/escalate", json={})
    assert resp.status_code == 500
    assert "inbox_upsert_failed" in resp.json()["detail"]


def test_wave_ii_post_endpoints_require_write_access(monkeypatch) -> None:
    monkeypatch.setenv("WEB_API_KEY", "secret")
    ctx, _cap = _build_wave_ii_ctx()
    client = _client(ctx)
    for path in (
        "/api/translator/mobile/register",
        "/api/translator/mobile/bind",
        "/api/translator/mobile/remove",
        "/api/translator/session/escalate",
    ):
        resp = client.post(path, json={})
        assert resp.status_code == 403, f"{path} should require write access"
