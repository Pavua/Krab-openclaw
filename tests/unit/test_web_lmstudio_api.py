# -*- coding: utf-8 -*-
"""
Тесты LM Studio / local model endpoints owner-панели Krab.

Покрываем три endpoint'а:
  GET  /api/model/local/status       — текущий статус локального рантайма
  POST /api/model/local/load-default — загрузить предпочтительную модель
  POST /api/model/local/unload       — выгрузить активную модель
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeRouter:
    """Минимальный роутер с методами LM Studio."""

    local_preferred_model: str = "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF"
    active_local_model: str | None = "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF"

    def get_model_info(self) -> dict:
        return {}

    async def _smart_load(self, model: str, reason: str = "") -> bool:
        # Успешная загрузка по умолчанию
        return True

    async def unload_local_model(self, model: str) -> bool:
        return True

    async def _evict_idle_models(self, needed_gb: float = 0.0) -> float:
        return 4.5


class _FakeRouterNoPreferred(_FakeRouter):
    """Роутер без предпочтительной модели (проверяем ошибку)."""

    local_preferred_model: str = ""
    active_local_model: str | None = None


class _FakeRouterLoadFail(_FakeRouter):
    """Роутер, где smart_load всегда возвращает False."""

    async def _smart_load(self, model: str, reason: str = "") -> bool:
        return False


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "local", "provider": "lmstudio", "model": "llama-3", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "local", "last_error_code": None}

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
        return {"language_pair": "es-ru", "enabled": False}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------

# Фиктивный ответ _resolve_local_runtime_truth — возвращает "живой" статус LM Studio
_TRUTH_LOADED = {
    "active_model": "lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF",
    "engine": "lmstudio",
    "runtime_url": "http://127.0.0.1:1234",
    "is_loaded": True,
    "runtime_reachable": True,
    "loaded_models": ["lmstudio-community/Meta-Llama-3.1-8B-Instruct-GGUF"],
    "probe_state": "up",
    "error": "",
}

_TRUTH_UNLOADED = {
    "active_model": "",
    "engine": "lmstudio",
    "runtime_url": "http://127.0.0.1:1234",
    "is_loaded": False,
    "runtime_reachable": False,
    "loaded_models": [],
    "probe_state": "down",
    "error": "no model loaded",
}


def _make_app(router=None) -> WebApp:
    """Создаёт WebApp с подменой зависимостей."""
    deps = {
        "router": router or _FakeRouter(),
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


def _client(router=None, truth=None) -> TestClient:
    """
    Возвращает TestClient с патчем _resolve_local_runtime_truth.
    truth — dict, который будет возвращён методом (по умолчанию _TRUTH_LOADED).
    """
    app_obj = _make_app(router=router)
    resolved = truth if truth is not None else _TRUTH_LOADED

    async def _fake_truth(r):
        return resolved

    app_obj._resolve_local_runtime_truth = _fake_truth
    return TestClient(app_obj.app)


# ---------------------------------------------------------------------------
# GET /api/model/local/status
# ---------------------------------------------------------------------------


def test_local_status_ok_loaded() -> None:
    """GET /api/model/local/status возвращает ok=True и status='loaded'."""
    resp = _client().get("/api/model/local/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "loaded"


def test_local_status_contains_model_name() -> None:
    """Поле model_name соответствует активной модели из truth."""
    resp = _client().get("/api/model/local/status")
    data = resp.json()
    assert "Meta-Llama" in data["model_name"]


def test_local_status_not_loaded() -> None:
    """Когда рантайм недоступен — status='not_loaded' и available=False."""
    resp = _client(truth=_TRUTH_UNLOADED).get("/api/model/local/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "not_loaded"
    assert data["details"]["available"] is False


def test_local_status_has_details_and_legacy() -> None:
    """Ответ содержит совместимые блоки details и status_legacy."""
    resp = _client().get("/api/model/local/status")
    data = resp.json()
    assert "details" in data
    assert "status_legacy" in data
    # Оба блока должны иметь одинаковое is_loaded
    assert data["details"]["is_loaded"] == data["status_legacy"]["is_loaded"]


# ---------------------------------------------------------------------------
# POST /api/model/local/load-default
# ---------------------------------------------------------------------------


def test_load_default_success() -> None:
    """POST /api/model/local/load-default загружает предпочтительную модель."""
    resp = _client().post("/api/model/local/load-default")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "Meta-Llama" in data["model"]


def test_load_default_no_preferred_returns_error() -> None:
    """Если preferred_model не настроен — ok=False и error='no_preferred_model_configured'."""
    with patch("src.config.config.LOCAL_PREFERRED_MODEL", "", create=True):
        resp = _client(router=_FakeRouterNoPreferred()).post("/api/model/local/load-default")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "no_preferred_model_configured"


def test_load_default_smart_load_fail() -> None:
    """Если _smart_load вернул False — ok=False в ответе."""
    resp = _client(router=_FakeRouterLoadFail()).post("/api/model/local/load-default")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


# ---------------------------------------------------------------------------
# POST /api/model/local/unload
# ---------------------------------------------------------------------------


def test_unload_active_model_success() -> None:
    """POST /api/model/local/unload выгружает активную модель."""
    resp = _client().post("/api/model/local/unload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Поле unloaded содержит имя модели
    assert "Meta-Llama" in data.get("unloaded", "")


def test_unload_no_active_model_evicts() -> None:
    """Если active_local_model не задана — используется _evict_idle_models."""
    router = _FakeRouter()
    router.active_local_model = None
    resp = _client(router=router).post("/api/model/local/unload")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Возвращается оценка освобождённой памяти
    assert "freed_gb_estimate" in data
