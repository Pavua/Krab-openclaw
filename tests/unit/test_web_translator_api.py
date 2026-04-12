# -*- coding: utf-8 -*-
"""
Тесты translator API endpoints в web_app.py.

Покрываем:
  GET  /api/translator/status
  GET  /api/translator/history
  POST /api/translator/session/toggle
  POST /api/translator/auto
  POST /api/translator/lang
  POST /api/translator/translate
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент без внешних вызовов."""

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


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    """Userbot-заглушка с полным набором translator-методов."""

    def __init__(self, session_status: str = "idle") -> None:
        self._session_status = session_status
        self._profile = {"language_pair": "es-ru", "enabled": True}
        self._session_state = {
            "session_status": session_status,
            "active_chats": [],
            "stats": {
                "total_translations": 5,
                "total_latency_ms": 2500,
            },
            "last_language_pair": "es-ru",
            "last_translated_original": "hola",
            "last_translated_translation": "привет",
        }

    def get_translator_runtime_profile(self) -> dict:
        return self._profile

    def get_translator_session_state(self) -> dict:
        return self._session_state

    def update_translator_session_state(self, **kwargs) -> None:
        """Имитация обновления session state; сохраняем переданные поля."""
        for k, v in kwargs.items():
            if k != "persist":
                self._session_state[k] = v

    def update_translator_runtime_profile(self, **kwargs) -> None:
        """Имитация обновления runtime profile."""
        for k, v in kwargs.items():
            if k != "persist":
                self._profile[k] = v

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика WebApp (паттерн _make_app)
# ---------------------------------------------------------------------------


def _make_app(*, kraab: _FakeKraab | None = None) -> WebApp:
    """Создаёт WebApp с полным набором заглушек в deps."""
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
        "kraab_userbot": kraab or _FakeKraab(),
    }
    return WebApp(deps, port=18090, host="127.0.0.1")


def _client(*, kraab: _FakeKraab | None = None) -> TestClient:
    """Возвращает TestClient с настроенным WebApp."""
    return TestClient(_make_app(kraab=kraab).app)


# ---------------------------------------------------------------------------
# GET /api/translator/status
# ---------------------------------------------------------------------------


def test_translator_status_ok() -> None:
    """GET /api/translator/status должен вернуть ok=True."""
    resp = _client().get("/api/translator/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_translator_status_contains_profile() -> None:
    """Ответ должен содержать profile с language_pair."""
    resp = _client().get("/api/translator/status")
    data = resp.json()
    assert "profile" in data
    assert data["profile"]["language_pair"] == "es-ru"


def test_translator_status_contains_session() -> None:
    """Ответ должен содержать session с session_status."""
    resp = _client().get("/api/translator/status")
    data = resp.json()
    assert "session" in data
    assert "session_status" in data["session"]


def test_translator_status_error_graceful() -> None:
    """При ошибке в kraab /api/translator/status возвращает ok=False без 500."""
    kraab = _FakeKraab()
    kraab.get_translator_runtime_profile = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    resp = _client(kraab=kraab).get("/api/translator/status")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/translator/history
# ---------------------------------------------------------------------------


def test_translator_history_ok() -> None:
    """GET /api/translator/history возвращает ok=True и статистику."""
    resp = _client().get("/api/translator/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_translations"] == 5


def test_translator_history_avg_latency() -> None:
    """avg_latency_ms должен быть 2500/5 = 500."""
    resp = _client().get("/api/translator/history")
    assert resp.json()["avg_latency_ms"] == 500


def test_translator_history_last_fields() -> None:
    """Ответ должен содержать last_pair, last_original, last_translation."""
    data = _client().get("/api/translator/history").json()
    assert data["last_pair"] == "es-ru"
    assert data["last_original"] == "hola"
    assert data["last_translation"] == "привет"


# ---------------------------------------------------------------------------
# POST /api/translator/session/toggle
# ---------------------------------------------------------------------------


def test_session_toggle_starts_when_idle(monkeypatch) -> None:
    """toggle при idle-сессии должен вернуть action=started."""
    # WEB_API_KEY пустой → write access без ключа
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab(session_status="idle")
    resp = _client(kraab=kraab).post("/api/translator/session/toggle", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "started"
    assert data["status"] == "active"


def test_session_toggle_stops_when_active(monkeypatch) -> None:
    """toggle при active-сессии должен вернуть action=stopped."""
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab(session_status="active")
    resp = _client(kraab=kraab).post("/api/translator/session/toggle", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "stopped"


def test_session_toggle_with_chat_id(monkeypatch) -> None:
    """toggle с chat_id должен включить active_chats."""
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab(session_status="idle")
    resp = _client(kraab=kraab).post("/api/translator/session/toggle", json={"chat_id": "123456"})
    data = resp.json()
    assert "123456" in data["active_chats"]


# ---------------------------------------------------------------------------
# POST /api/translator/auto
# ---------------------------------------------------------------------------


def test_translator_auto_sets_auto_detect(monkeypatch) -> None:
    """POST /api/translator/auto должен переключить пару в auto-detect."""
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    resp = _client(kraab=kraab).post("/api/translator/auto")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["language_pair"] == "auto-detect"
    # проверяем что profile действительно обновился
    assert kraab._profile["language_pair"] == "auto-detect"


# ---------------------------------------------------------------------------
# POST /api/translator/lang
# ---------------------------------------------------------------------------


def test_translator_lang_valid_pair(monkeypatch) -> None:
    """POST /api/translator/lang с допустимой парой должен вернуть ok=True."""
    monkeypatch.setenv("WEB_API_KEY", "")
    kraab = _FakeKraab()
    resp = _client(kraab=kraab).post("/api/translator/lang", json={"language_pair": "en-ru"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["language_pair"] == "en-ru"


def test_translator_lang_invalid_pair(monkeypatch) -> None:
    """POST /api/translator/lang с неизвестной парой должен вернуть ok=False."""
    monkeypatch.setenv("WEB_API_KEY", "")
    resp = _client().post("/api/translator/lang", json={"language_pair": "zz-xx"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# POST /api/translator/translate
# ---------------------------------------------------------------------------


def test_translator_translate_empty_text(monkeypatch) -> None:
    """translate без текста должен вернуть ok=False с ошибкой."""
    monkeypatch.setenv("WEB_API_KEY", "")
    resp = _client().post("/api/translator/translate", json={"text": ""})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "text required" in data["error"]


def test_translator_translate_calls_engine(monkeypatch) -> None:
    """translate с текстом должен вызвать translate_text и вернуть результат."""
    monkeypatch.setenv("WEB_API_KEY", "")

    # Создаём фейковый результат перевода
    fake_result = MagicMock()
    fake_result.original = "hola"
    fake_result.translated = "привет"
    fake_result.src_lang = "es"
    fake_result.tgt_lang = "ru"
    fake_result.latency_ms = 120
    fake_result.model_id = "gemini-test"

    with (
        patch("src.core.language_detect.detect_language", return_value="es"),
        patch("src.core.language_detect.resolve_translation_pair", return_value=("es", "ru")),
        patch("src.core.translator_engine.translate_text", new=AsyncMock(return_value=fake_result)),
        patch("src.openclaw_client.openclaw_client", MagicMock()),
    ):
        resp = _client().post(
            "/api/translator/translate",
            json={"text": "hola", "src_lang": "es", "tgt_lang": "ru"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["translated"] == "привет"
    assert data["src_lang"] == "es"
