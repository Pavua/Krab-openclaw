# -*- coding: utf-8 -*-
"""
Unit tests for ``src.modules.web_routers.models_admin_router`` — Wave 144.

Покрывает:
- GET  /api/models/registry — shape, providers, health overlay, current routing
- POST /api/admin/model/switch — happy path, auth (403), validation (400),
                                 set_provider vs set_model branching
- GET  /admin/models — HTML render
- Graceful degradation если LM Studio probe бросает / возвращает down

Используется чистый FastAPI + TestClient, без полного WebApp. model_manager
+ openclaw_client patched через unittest.mock.patch для изоляции.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.web_routers._context import RouterContext
from src.modules.web_routers.models_admin_router import build_models_admin_router

# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeMM:
    """Stub ``model_manager`` singleton — записывает set_model/set_provider."""

    def __init__(self) -> None:
        self.active_model_id = "google-vertex/gemini-3-pro-preview"
        self.last_set_model: str | None = None
        self.last_set_provider: str | None = None

    def set_model(self, model_id: str) -> None:
        self.last_set_model = model_id
        self.active_model_id = model_id

    def set_provider(self, provider: str) -> None:
        self.last_set_provider = provider
        self.active_model_id = f"mode:{provider}"


class _FakeOC:
    def __init__(self, route: dict[str, Any] | None = None) -> None:
        self._route = route or {
            "channel": "cloud",
            "model": "google-vertex/gemini-3-pro-preview",
            "provider": "google-vertex",
            "status": "ok",
            "timestamp": 1778617439,
        }

    def get_last_runtime_route(self) -> dict[str, Any]:
        return dict(self._route)


class _FakeQuarantine:
    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self._entries = entries or []

    def list_entries(self) -> list[dict[str, Any]]:
        return list(self._entries)


class _FakeBlackBox:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def log_event(self, kind: str, detail: str) -> None:
        self.events.append((kind, detail))


# ── Fixture builders ───────────────────────────────────────────────────────


def _build_ctx(
    *,
    local_truth: dict[str, Any] | None = None,
    local_raises: Exception | None = None,
    black_box: Any | None = None,
) -> RouterContext:
    """Создаёт минимальный RouterContext для tests."""

    def _local_helper(_router_obj: Any) -> dict[str, Any]:
        if local_raises is not None:
            raise local_raises
        return local_truth or {}

    deps: dict[str, Any] = {
        "router": object(),  # placeholder; helper не использует методы
        "resolve_local_runtime_truth_helper": _local_helper,
    }
    if black_box is not None:
        deps["black_box"] = black_box

    return RouterContext(
        deps=deps,
        project_root=Path("/tmp"),
        web_api_key_fn=lambda: "",
        assert_write_access_fn=lambda *_a, **_kw: None,
    )


def _client(
    ctx: RouterContext | None = None,
    *,
    quarantine: _FakeQuarantine | None = None,
    fake_mm: _FakeMM | None = None,
    fake_oc: _FakeOC | None = None,
    codex_disabled: bool = False,
) -> tuple[TestClient, dict[str, Any]]:
    """Возвращает (TestClient, accessors-dict).

    Все singleton-ы patched через ``patch.dict / patch`` в контексте теста —
    но возвращаем references чтобы tests могли инспектировать состояние.
    """
    mm = fake_mm or _FakeMM()
    oc = fake_oc or _FakeOC()
    q = quarantine or _FakeQuarantine()

    app = FastAPI()
    app.include_router(build_models_admin_router(ctx or _build_ctx()))
    client = TestClient(app)
    return client, {"mm": mm, "oc": oc, "quarantine": q, "codex_disabled": codex_disabled}


def _apply_patches(refs: dict[str, Any]):
    """Возвращает контекстный менеджер с активными patch-ами для refs."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("src.model_manager.model_manager", refs["mm"]))
    stack.enter_context(patch("src.openclaw_client.openclaw_client", refs["oc"]))
    stack.enter_context(
        patch("src.core.provider_quarantine.provider_quarantine", refs["quarantine"])
    )
    stack.enter_context(
        patch(
            "src.integrations.codex_quota_state.is_codex_disabled",
            lambda: refs["codex_disabled"],
        )
    )
    stack.enter_context(
        patch(
            "src.core.openclaw_runtime_models.get_runtime_primary_model",
            lambda: refs["mm"].active_model_id,
        )
    )
    return stack


# ── GET /api/models/registry tests ──────────────────────────────────────────


def test_registry_returns_provider_grouped_shape() -> None:
    """Базовая форма: ok=true, current, providers list, history list."""
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["providers"], list)
    assert isinstance(data["history"], list)
    # Все провайдеры присутствуют (4 cloud + 1 lm-studio).
    provider_ids = [p["id"] for p in data["providers"]]
    assert "google-vertex" in provider_ids
    assert "anthropic-vertex" in provider_ids
    assert "codex-cli" in provider_ids
    assert "google-gemini-cli" in provider_ids
    assert "lm-studio" in provider_ids


def test_registry_current_routing_reflects_active_model() -> None:
    """current.model совпадает с active_model_id из mm."""
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    data = resp.json()
    assert data["current"]["model"] == "google-vertex/gemini-3-pro-preview"
    assert data["current"]["channel"] == "cloud"
    assert data["current"]["since"] is not None  # ISO timestamp


def test_registry_marks_active_model_in_provider_list() -> None:
    """Активная модель помечена is_active=True в provider list."""
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    data = resp.json()
    vertex = next(p for p in data["providers"] if p["id"] == "google-vertex")
    active_models = [m for m in vertex["models"] if m["is_active"]]
    assert len(active_models) == 1
    assert active_models[0]["id"] == "google-vertex/gemini-3-pro-preview"


def test_registry_overlays_quarantine_health() -> None:
    """Provider с quarantine получает status=quarantined."""
    quarantine = _FakeQuarantine(
        entries=[{"provider": "anthropic-vertex", "quarantined": True}]
    )
    client, refs = _client(quarantine=quarantine)
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    data = resp.json()
    anth = next(p for p in data["providers"] if p["id"] == "anthropic-vertex")
    assert anth["available"] is False
    for m in anth["models"]:
        assert m["status"] == "quarantined"
        assert "quarantine" in m["status_detail"].lower()


def test_registry_overlays_codex_quota_exhausted() -> None:
    """Когда codex disabled — codex-cli models получают quota_exhausted."""
    client, refs = _client(codex_disabled=True)
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    data = resp.json()
    assert data["codex_accounts_exhausted"] is True
    codex = next(p for p in data["providers"] if p["id"] == "codex-cli")
    for m in codex["models"]:
        assert m["status"] == "quota_exhausted"


def test_registry_lm_studio_section_with_loaded_models() -> None:
    """LM Studio probe возвращает loaded → status=loaded, actions include unload."""
    local_truth = {
        "runtime_reachable": True,
        "is_loaded": True,
        "active_model": "gemma-4-26b-a4b-it-optiq",
        "loaded_models": ["gemma-4-26b-a4b-it-optiq"],
        "runtime_url": "http://127.0.0.1:1234",
        "error": "",
    }
    ctx = _build_ctx(local_truth=local_truth)
    client, refs = _client(ctx=ctx)
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    data = resp.json()
    lm = next(p for p in data["providers"] if p["id"] == "lm-studio")
    assert lm["available"] is True
    assert lm["runtime_url"] == "http://127.0.0.1:1234"
    loaded_entries = [m for m in lm["models"] if m["status"] == "loaded"]
    assert len(loaded_entries) >= 1
    assert any(m["id"] == "gemma-4-26b-a4b-it-optiq" for m in loaded_entries)
    # Для loaded моделей — actions содержат unload, не load.
    target = next(m for m in lm["models"] if m["id"] == "gemma-4-26b-a4b-it-optiq")
    assert "unload" in target["actions"]
    assert "load" not in target["actions"]


def test_registry_lm_studio_probe_failure_graceful() -> None:
    """Если probe бросает — LM Studio section возвращается, available=False."""
    ctx = _build_ctx(local_raises=RuntimeError("lm studio offline"))
    client, refs = _client(ctx=ctx)
    with _apply_patches(refs):
        resp = client.get("/api/models/registry")
    assert resp.status_code == 200
    data = resp.json()
    lm = next(p for p in data["providers"] if p["id"] == "lm-studio")
    assert lm["available"] is False
    # Все модели — not_loaded.
    for m in lm["models"]:
        assert m["status"] == "not_loaded"


# ── POST /api/admin/model/switch tests ─────────────────────────────────────


def test_switch_set_model_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Передан model — model_manager.set_model вызывается, action=set_model."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    bb = _FakeBlackBox()
    ctx = _build_ctx(black_box=bb)
    client, refs = _client(ctx=ctx)
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"provider": "google-vertex", "model": "google-vertex/gemini-2.5-pro"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["action"] == "set_model"
    assert body["model"] == "google-vertex/gemini-2.5-pro"
    assert refs["mm"].last_set_model == "google-vertex/gemini-2.5-pro"
    # Black box получил событие.
    assert any(ev[0] == "model_switch" for ev in bb.events)


def test_switch_set_provider_mode_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Передан только provider=auto/local/cloud — set_provider, action=set_provider."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"provider": "cloud"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["action"] == "set_provider"
    assert refs["mm"].last_set_provider == "cloud"


def test_switch_without_auth_returns_403(monkeypatch: pytest.MonkeyPatch) -> None:
    """С WEB_API_KEY и без header/token → 403."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"model": "google-vertex/gemini-2.5-pro"},
        )
    assert resp.status_code == 403


def test_switch_with_valid_auth_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """С правильным X-Krab-Web-Key → 200."""
    monkeypatch.setenv("WEB_API_KEY", "secret-key")
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"model": "google-vertex/gemini-2.5-pro"},
            headers={"X-Krab-Web-Key": "secret-key"},
        )
    assert resp.status_code == 200


def test_switch_empty_body_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой body → 400 provider_or_model_required."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post("/api/admin/model/switch", json={})
    assert resp.status_code == 400
    assert "provider_or_model_required" in resp.json()["detail"]


def test_switch_unknown_provider_in_model_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model с unknown provider prefix → 400."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"model": "unknown-provider/some-model"},
        )
    assert resp.status_code == 400
    assert "model_unknown" in resp.json()["detail"]


def test_switch_lmstudio_prefix_accepted_session50_p35(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session 50 P3.5: `lmstudio/` prefix (без дефиса, OpenClaw models.json
    convention) должен быть в known_prefixes whitelist — раньше отбрасывался
    как `model_unknown`, хотя `lm-studio-local/` (с дефисом) проходил.
    Включает `@`-suffix (LM Studio quant convention) для полноты regression.
    """
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"model": "lmstudio/gemma-4-26b-a4b-it@4bit"},
        )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["model"] == "lmstudio/gemma-4-26b-a4b-it@4bit"


def test_switch_invalid_provider_mode_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provider не auto/local/cloud без model → 400."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    client, refs = _client()
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"provider": "google-vertex"},  # без model
        )
    assert resp.status_code == 400


def test_switch_value_error_from_mm_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set_model бросает ValueError → 400 detail = exc text."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)
    bad_mm = _FakeMM()

    def _raise(_: str) -> None:
        raise ValueError("bad model")

    bad_mm.set_model = _raise  # type: ignore[assignment]
    client, refs = _client(fake_mm=bad_mm)
    with _apply_patches(refs):
        resp = client.post(
            "/api/admin/model/switch",
            json={"model": "google-vertex/gemini-2.5-pro"},
        )
    assert resp.status_code == 400
    assert "bad model" in resp.json()["detail"]


# ── GET /admin/models tests ────────────────────────────────────────────────


def test_admin_models_page_returns_html() -> None:
    """HTML страница рендерится, содержит ключевые UI элементы."""
    client, _refs = _client()
    resp = client.get("/admin/models")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    body = resp.text
    assert "Model Picker" in body
    assert "/api/models/registry" in body  # endpoint referenced by JS
    assert "/api/admin/model/switch" in body
    # No-store cache header.
    assert "no-store" in resp.headers.get("cache-control", "")
