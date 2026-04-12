# -*- coding: utf-8 -*-
"""
Тесты voice API endpoint'ов web-панели Krab.

Покрываем:
  GET  /api/voice/profile         — голосовой профиль userbot
  GET  /api/voice/runtime         — runtime-сводка voice-модуля
  POST /api/voice/toggle          — переключение voice_mode (требует write-key)
"""

from __future__ import annotations

import contextlib
import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "google/gemini-test"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    """Минимальный userbot-stub с voice-атрибутами."""

    voice_mode: bool = False

    def get_voice_runtime_profile(self) -> dict:
        return {
            "tts_enabled": self.voice_mode,
            "speed": 1.0,
            "voice": "default",
            "delivery": "telegram",
        }

    def update_voice_runtime_profile(
        self,
        *,
        enabled=None,
        speed=None,
        voice=None,
        delivery=None,
        persist=False,
    ) -> dict:
        profile = self.get_voice_runtime_profile()
        if enabled is not None:
            self.voice_mode = bool(enabled)
            profile["tts_enabled"] = self.voice_mode
        if speed is not None:
            profile["speed"] = speed
        if voice is not None:
            profile["voice"] = voice
        if delivery is not None:
            profile["delivery"] = delivery
        return profile

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": False}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Фабрика клиента
# ---------------------------------------------------------------------------


def _make_app(kraab=None) -> WebApp:
    """Создаёт WebApp с заглушками."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": None,
        "krab_ear_client": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
        "kraab_userbot": kraab or _FakeKraab(),
    }
    return WebApp(deps, port=18091, host="127.0.0.1")


@contextlib.contextmanager
def _with_client(kraab=None, web_key: str = ""):
    """Контекст-менеджер: TestClient + активный WEB_API_KEY в env."""
    app = _make_app(kraab=kraab)
    # Формируем чистый env: если web_key задан — ставим, иначе убираем
    if web_key:
        env_override = {"WEB_API_KEY": web_key}
        with patch.dict(os.environ, env_override):
            yield TestClient(app.app)
    else:
        # Убеждаемся, что WEB_API_KEY не попал из внешнего окружения
        saved = os.environ.pop("WEB_API_KEY", None)
        try:
            yield TestClient(app.app)
        finally:
            if saved is not None:
                os.environ["WEB_API_KEY"] = saved


# ---------------------------------------------------------------------------
# GET /api/voice/profile
# ---------------------------------------------------------------------------


def test_voice_profile_ok() -> None:
    """GET /api/voice/profile возвращает ok=True и поле profile."""
    with _with_client() as c:
        resp = c.get("/api/voice/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "profile" in data


def test_voice_profile_contains_tts_enabled() -> None:
    """Профиль содержит поле tts_enabled."""
    with _with_client() as c:
        resp = c.get("/api/voice/profile")
    assert "tts_enabled" in resp.json()["profile"]


def test_voice_profile_reflects_voice_mode() -> None:
    """Профиль отражает текущее состояние voice_mode userbot'а."""
    kraab = _FakeKraab()
    kraab.voice_mode = True
    with _with_client(kraab=kraab) as c:
        resp = c.get("/api/voice/profile")
    assert resp.json()["profile"]["tts_enabled"] is True


# ---------------------------------------------------------------------------
# GET /api/voice/runtime
# ---------------------------------------------------------------------------


def test_voice_runtime_ok() -> None:
    """GET /api/voice/runtime возвращает ok=True и поле voice."""
    with _with_client() as c:
        resp = c.get("/api/voice/runtime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "voice" in data


def test_voice_runtime_without_kraab() -> None:
    """Если kraab_userbot не поддерживает get_voice_runtime_profile — ok=False."""
    kraab_no_voice = MagicMock(spec=[])  # нет метода get_voice_runtime_profile
    with _with_client(kraab=kraab_no_voice) as c:
        resp = c.get("/api/voice/runtime")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# POST /api/voice/toggle
# ---------------------------------------------------------------------------


def test_voice_toggle_no_auth_works() -> None:
    """POST /api/voice/toggle без web_key разрешён (ключ не настроен)."""
    with _with_client(web_key="") as c:
        resp = c.post("/api/voice/toggle", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["voice_enabled"] is True


def test_voice_toggle_forbids_wrong_key() -> None:
    """POST /api/voice/toggle с неверным ключом → 403."""
    with _with_client(web_key="secret") as c:
        resp = c.post(
            "/api/voice/toggle",
            json={"enabled": True},
            headers={"X-Krab-Web-Key": "wrong"},
        )
    assert resp.status_code == 403


def test_voice_toggle_accepts_correct_key() -> None:
    """POST /api/voice/toggle с правильным ключом → 200 и voice_enabled."""
    with _with_client(web_key="mysecret") as c:
        resp = c.post(
            "/api/voice/toggle",
            json={"enabled": True},
            headers={"X-Krab-Web-Key": "mysecret"},
        )
    assert resp.status_code == 200
    assert resp.json()["voice_enabled"] is True


def test_voice_toggle_flips_state() -> None:
    """POST /api/voice/toggle без поля enabled инвертирует текущий state."""
    kraab = _FakeKraab()
    kraab.voice_mode = False
    with _with_client(kraab=kraab, web_key="") as c:
        r1 = c.post("/api/voice/toggle", json={})  # должен включить
        r2 = c.post("/api/voice/toggle", json={})  # должен выключить
    assert r1.json()["voice_enabled"] is True
    assert r2.json()["voice_enabled"] is False
