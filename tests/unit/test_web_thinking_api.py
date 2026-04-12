# -*- coding: utf-8 -*-
"""
Тесты thinking/depth API endpoint'ов owner-панели Krab.

Покрываем:
  GET  /api/thinking/status   — текущий режим и список допустимых режимов
  POST /api/thinking/set      — смена глобального thinking_default
  GET  /api/depth/status      — алиас depth == thinking_default
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Минимальные заглушки зависимостей
# ---------------------------------------------------------------------------

_THINKING_MODES = ["off", "minimal", "low", "medium", "high", "xhigh", "adaptive"]

_FAKE_RUNTIME_CONTROLS = {
    "primary": "google/gemini-test",
    "fallbacks": [],
    "context_tokens": 128000,
    "thinking_default": "off",
    "main_max_concurrent": 4,
    "subagent_max_concurrent": 8,
    "execution_preset": "parallel",
    "execution_presets": [],
    "thinking_modes": _THINKING_MODES,
    "chain_items": [],
    "max_fallback_slots": 8,
}


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "google/gemini-test"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {}

    def get_translator_session_state(self) -> dict:
        return {}

    def get_voice_runtime_profile(self) -> dict:
        return {}

    def get_runtime_state(self) -> dict:
        return {}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True}

    async def capabilities_report(self) -> dict:
        return {"ok": True}


def _make_app() -> WebApp:
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# GET /api/thinking/status
# ---------------------------------------------------------------------------


def test_thinking_status_ok_field() -> None:
    """Эндпоинт должен вернуть ok=True."""
    with patch.object(
        WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
    ):
        resp = _client().get("/api/thinking/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_thinking_status_contains_thinking_default() -> None:
    """Ответ содержит поле thinking_default."""
    fake = dict(_FAKE_RUNTIME_CONTROLS, thinking_default="medium")
    with patch.object(WebApp, "_build_openclaw_runtime_controls", return_value=fake):
        data = _client().get("/api/thinking/status").json()
    assert data["thinking_default"] == "medium"


def test_thinking_status_contains_thinking_modes() -> None:
    """Ответ содержит полный список допустимых режимов."""
    with patch.object(
        WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
    ):
        data = _client().get("/api/thinking/status").json()
    assert set(_THINKING_MODES).issubset(set(data["thinking_modes"]))


def test_thinking_status_contains_chain_items() -> None:
    """Ответ содержит chain_items (может быть пустым)."""
    with patch.object(
        WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
    ):
        data = _client().get("/api/thinking/status").json()
    assert "chain_items" in data
    assert isinstance(data["chain_items"], list)


# ---------------------------------------------------------------------------
# POST /api/thinking/set
# ---------------------------------------------------------------------------


def test_thinking_set_valid_mode() -> None:
    """POST с корректным режимом должен вернуть ok=True и новый thinking_default."""
    applied = {"thinking_default": "high", "changed": {}}
    with (
        patch.object(
            WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
        ),
        patch.object(WebApp, "_apply_openclaw_runtime_controls", return_value=applied),
    ):
        resp = _client().post("/api/thinking/set", json={"mode": "high"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["thinking_default"] == "high"


def test_thinking_set_invalid_mode_returns_400() -> None:
    """POST с неизвестным режимом должен вернуть 400."""
    resp = _client().post("/api/thinking/set", json={"mode": "turbo"})
    assert resp.status_code == 400
    assert "invalid_thinking_mode" in resp.json()["detail"]


def test_thinking_set_off_is_valid() -> None:
    """Режим 'off' является допустимым и не должен вызывать ошибку валидации."""
    applied = {"thinking_default": "off", "changed": {}}
    with (
        patch.object(
            WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
        ),
        patch.object(WebApp, "_apply_openclaw_runtime_controls", return_value=applied),
    ):
        resp = _client().post("/api/thinking/set", json={"mode": "off"})
    assert resp.status_code == 200
    assert resp.json()["thinking_default"] == "off"


def test_thinking_set_auto_alias_rejected() -> None:
    """Устаревший алиас 'auto' нормализуется в 'adaptive' — должен принять."""
    applied = {"thinking_default": "adaptive", "changed": {}}
    with (
        patch.object(
            WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
        ),
        patch.object(WebApp, "_apply_openclaw_runtime_controls", return_value=applied),
    ):
        resp = _client().post("/api/thinking/set", json={"mode": "auto"})
    # auto → adaptive — допустимый алиас
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/depth/status
# ---------------------------------------------------------------------------


def test_depth_status_ok_field() -> None:
    """GET /api/depth/status должен вернуть ok=True."""
    with patch.object(
        WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
    ):
        resp = _client().get("/api/depth/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_depth_status_contains_depth_and_thinking_default() -> None:
    """Ответ содержит поля depth и thinking_default с одинаковым значением."""
    fake = dict(_FAKE_RUNTIME_CONTROLS, thinking_default="adaptive")
    with patch.object(WebApp, "_build_openclaw_runtime_controls", return_value=fake):
        data = _client().get("/api/depth/status").json()
    assert data["depth"] == "adaptive"
    assert data["thinking_default"] == "adaptive"
    assert data["depth"] == data["thinking_default"]


def test_depth_status_contains_available_modes() -> None:
    """Ответ содержит available_modes со всеми допустимыми режимами."""
    with patch.object(
        WebApp, "_build_openclaw_runtime_controls", return_value=_FAKE_RUNTIME_CONTROLS
    ):
        data = _client().get("/api/depth/status").json()
    assert "available_modes" in data
    assert set(_THINKING_MODES).issubset(set(data["available_modes"]))
