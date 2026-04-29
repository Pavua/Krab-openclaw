# -*- coding: utf-8 -*-
"""
Тесты POST /api/model/switch.

Покрываем:
- переключение на cloud-модель (google/…);
- переключение на local-модель (LM Studio ID);
- невалидный ID → 400;
- структура успешного ответа.

Почему отдельный файл: session 14 пофиксила endpoint, который вызывал
несуществующий `_mm.set_model()`. Тесты фиксируют контракт API и защищают
от регрессий при будущем рефакторинге ModelManager.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Минимальные fakes (копируют интерфейс из test_web_model_api.py,
# чтобы оставаться независимыми от чужих test-фикстур).
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-test",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _RecordingModelManager:
    """Записывает вызовы set_provider / set_model для верификации контракта."""

    def __init__(self, *, raise_on_model: str | None = None) -> None:
        self.provider_calls: list[str] = []
        self.model_calls: list[str] = []
        self._raise_on_model = raise_on_model
        self.active_model_id: str = "google/gemini-test"

    def set_provider(self, provider: str) -> None:
        self.provider_calls.append(provider)
        self.active_model_id = f"provider:{provider}"

    def set_model(self, model: str) -> None:
        if self._raise_on_model is not None and model == self._raise_on_model:
            raise ValueError(f"unknown model: {model}")
        self.model_calls.append(model)
        self.active_model_id = model


def _make_client() -> TestClient:
    deps = {
        "router": None,
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
    app = WebApp(deps, port=18092, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_switch_to_cloud_model_invokes_set_model() -> None:
    """POST с cloud-model-id (google/…) должен дергать set_model."""
    fake_mm = _RecordingModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "google/gemini-3-pro-preview"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert fake_mm.model_calls == ["google/gemini-3-pro-preview"]
    assert fake_mm.provider_calls == []


def test_switch_to_local_model_invokes_set_model() -> None:
    """POST с LM Studio ID (например, `local/qwen3-8b`) должен дергать set_model."""
    fake_mm = _RecordingModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "local/qwen3-8b-mlx"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fake_mm.model_calls == ["local/qwen3-8b-mlx"]


def test_switch_to_invalid_model_returns_400() -> None:
    """Если set_model поднимает ValueError, endpoint отдаёт HTTP 400."""
    fake_mm = _RecordingModelManager(raise_on_model="totally-bogus-id")
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "totally-bogus-id"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 400
    # detail ожидается в стандартном FastAPI-формате
    assert "unknown model" in resp.json().get("detail", "").lower()


def test_switch_response_structure_ok_model_active() -> None:
    """Успешный ответ должен содержать ok=True, model и active."""
    fake_mm = _RecordingModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "google/gemini-3-flash-preview"},
            headers={"X-Krab-Web-Key": ""},
        )
    data = resp.json()
    assert set(data.keys()) >= {"ok", "model", "active"}
    assert data["ok"] is True
    assert data["model"] == "google/gemini-3-flash-preview"
    assert data["active"] == "google/gemini-3-flash-preview"


def test_switch_auto_uses_set_provider() -> None:
    """model='auto' должен маршрутизироваться в set_provider, не в set_model."""
    fake_mm = _RecordingModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={"model": "auto"},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert fake_mm.provider_calls == ["auto"]
    assert fake_mm.model_calls == []


def test_switch_missing_model_field() -> None:
    """Пустой payload — ok=False, 200 (чтобы UI показал понятную ошибку)."""
    fake_mm = _RecordingModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _make_client().post(
            "/api/model/switch",
            json={},
            headers={"X-Krab-Web-Key": ""},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "required" in body.get("error", "").lower()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
