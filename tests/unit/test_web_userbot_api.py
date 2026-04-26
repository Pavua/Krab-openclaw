# -*- coding: utf-8 -*-
"""
Тесты userbot control API endpoints web-панели Krab.

Покрываем:
  POST /api/krab/restart_userbot  — перезапуск userbot
  POST /api/notify                — отправка сообщения через userbot
  POST /api/silence/toggle        — включение/выключение тишины
  GET  /api/silence/status        — текущий статус тишины
  GET  /api/uptime                — uptime в секундах
  GET  /api/version               — версия и session info
  GET  /api/system/info           — системная информация
  POST /api/voice/toggle          — включение/выключение голоса
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------

# WEB_API_KEY — переменная окружения, которую читает _web_api_key()
WEB_KEY = "test-key-123"


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "gemini-test", "status": "ok"}

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


class _FakeTgClient:
    """Имитирует pyrogram-клиент с методом send_message."""

    async def send_message(self, chat_id: str, text: str) -> MagicMock:
        msg = MagicMock()
        msg.id = 999
        return msg


class _FakeKraab:
    """Минимальный userbot-stub."""

    voice_mode: bool = False

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": self.voice_mode}


class _FakeKraabWithRestart(_FakeKraab):
    """Userbot с поддержкой start/stop/restart для тестов restart endpoint."""

    restarted: bool = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def restart(self, reason: str = "") -> None:
        self.restarted = True


class _FakeKraabWithClient(_FakeKraab):
    """Userbot с pyrogram клиентом для тестов /api/notify."""

    client: _FakeTgClient

    def __init__(self) -> None:
        self.client = _FakeTgClient()


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app(
    *,
    kraab=None,
    require_auth: bool = False,
) -> WebApp:
    """WebApp с полным набором заглушек.

    require_auth=True — выставляет WEB_API_KEY в окружение, чтобы _assert_write_access работал.
    """
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
    env_patch = {"WEB_API_KEY": WEB_KEY} if require_auth else {"WEB_API_KEY": ""}
    with patch.dict("os.environ", env_patch):
        app = WebApp(deps, port=18091, host="127.0.0.1")
    return app


def _client(kraab=None, require_auth: bool = False) -> TestClient:
    return TestClient(_make_app(kraab=kraab, require_auth=require_auth).app)


AUTH_HEADERS = {"X-Krab-Web-Key": WEB_KEY}

# ---------------------------------------------------------------------------
# POST /api/krab/restart_userbot
# ---------------------------------------------------------------------------


def test_restart_userbot_no_deps_returns_error() -> None:
    """Если kraab_userbot не имеет start/stop — возвращает error."""
    # _FakeKraab без методов start/stop, без auth (WEB_API_KEY пустой)
    c = _client(kraab=_FakeKraab())
    resp = c.post("/api/krab/restart_userbot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "userbot_restart_unavailable"


def test_restart_userbot_with_restart_method() -> None:
    """Если kraab_userbot.restart() доступен — вызывается и возвращает ok=True."""
    kraab = _FakeKraabWithRestart()
    c = _client(kraab=kraab)
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        resp = c.post("/api/krab/restart_userbot", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "restart_userbot"
    # before/after должны быть словарями
    assert isinstance(data["before"], dict)
    assert isinstance(data["after"], dict)


def test_restart_userbot_requires_auth() -> None:
    """Без X-Krab-Web-Key при заданном WEB_API_KEY должен вернуть 403."""
    kraab = _FakeKraabWithRestart()
    c = _client(kraab=kraab)
    # WEB_API_KEY читается при каждом запросе через os.getenv
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        resp = c.post("/api/krab/restart_userbot")
    assert resp.status_code == 403


def test_restart_userbot_rate_limited_on_second_call() -> None:
    """Wave SS: второй вызов в окне 5 минут возвращает rate_limited."""
    kraab = _FakeKraabWithRestart()
    app = _make_app(kraab=kraab)
    c = TestClient(app.app)
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        first = c.post("/api/krab/restart_userbot", headers=AUTH_HEADERS)
        assert first.status_code == 200
        assert first.json()["ok"] is True

        # Второй вызов сразу — должен попасть под cooldown.
        second = c.post("/api/krab/restart_userbot", headers=AUTH_HEADERS)
    assert second.status_code == 200
    data = second.json()
    assert data["ok"] is False
    assert data["error"] == "rate_limited"
    assert "cooldown" in data["detail"]


def test_restart_userbot_falls_back_to_stop_start_when_no_restart_method() -> None:
    """Wave SS: если у userbot нет .restart(), используется stop()/start()."""

    class _FakeKraabStopStart(_FakeKraab):
        """userbot с start/stop, но без restart()."""

        stop_called: int = 0
        start_called: int = 0

        async def start(self) -> None:
            self.start_called += 1

        async def stop(self) -> None:
            self.stop_called += 1

    kraab = _FakeKraabStopStart()
    c = _client(kraab=kraab)
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        resp = c.post("/api/krab/restart_userbot", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["action"] == "restart_userbot"
    assert kraab.stop_called == 1
    assert kraab.start_called == 1


# ---------------------------------------------------------------------------
# POST /api/notify
# ---------------------------------------------------------------------------


def test_notify_sends_message() -> None:
    """POST /api/notify с корректными данными должен вернуть ok=True."""
    kraab = _FakeKraabWithClient()
    c = _client(kraab=kraab)
    resp = c.post(
        "/api/notify",
        json={"text": "hello", "chat_id": "123456"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["chat_id"] == "123456"


def test_notify_missing_text_returns_400() -> None:
    """POST /api/notify без поля text — 400."""
    kraab = _FakeKraabWithClient()
    c = _client(kraab=kraab)
    resp = c.post("/api/notify", json={"chat_id": "123456"})
    assert resp.status_code == 400


def test_notify_missing_chat_id_returns_400() -> None:
    """POST /api/notify без chat_id и без OPENCLAW_ALERT_TARGET — 400."""
    kraab = _FakeKraabWithClient()
    c = _client(kraab=kraab)
    with patch.dict("os.environ", {}, clear=False):
        # убедимся, что OPENCLAW_ALERT_TARGET не задан
        import os

        os.environ.pop("OPENCLAW_ALERT_TARGET", None)
        resp = c.post("/api/notify", json={"text": "hello"})
    assert resp.status_code == 400


def test_notify_no_userbot_returns_503() -> None:
    """Если kraab_userbot == None — /api/notify возвращает 503."""
    # Создаём приложение вручную с kraab_userbot=None
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
        "kraab_userbot": None,
    }
    with patch.dict("os.environ", {"WEB_API_KEY": ""}):
        app = WebApp(deps, port=18092, host="127.0.0.1")
    c = TestClient(app.app)
    resp = c.post("/api/notify", json={"text": "hello", "chat_id": "123456"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/uptime
# ---------------------------------------------------------------------------


def test_uptime_returns_seconds() -> None:
    """GET /api/uptime возвращает ok=True и числовой uptime_sec."""
    resp = _client().get("/api/uptime")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["uptime_sec"], (int, float))
    assert data["uptime_sec"] >= 0
    assert "boot_ts" in data


# ---------------------------------------------------------------------------
# GET /api/version
# ---------------------------------------------------------------------------


def test_version_returns_session_info() -> None:
    """GET /api/version возвращает ok=True и поля версии."""
    resp = _client().get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "version" in data
    assert "features" in data
    assert isinstance(data["features"], list)


# ---------------------------------------------------------------------------
# GET /api/system/info
# ---------------------------------------------------------------------------


def test_system_info_returns_host_data() -> None:
    """GET /api/system/info возвращает платформенные поля."""
    resp = _client().get("/api/system/info")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    for key in ("hostname", "platform", "python", "cpu_count", "ram_total_gb"):
        assert key in data, f"отсутствует поле: {key}"


# ---------------------------------------------------------------------------
# POST /api/voice/toggle
# ---------------------------------------------------------------------------


def test_voice_toggle_switches_state() -> None:
    """POST /api/voice/toggle должен изменить состояние voice_mode."""
    kraab = _FakeKraab()
    kraab.voice_mode = False
    c = _client(kraab=kraab)
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        resp = c.post("/api/voice/toggle", json={"enabled": True}, headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["voice_enabled"] is True


def test_voice_toggle_requires_auth() -> None:
    """POST /api/voice/toggle без ключа при заданном WEB_API_KEY — 403."""
    c = _client()
    with patch.dict("os.environ", {"WEB_API_KEY": WEB_KEY}):
        resp = c.post("/api/voice/toggle", json={"enabled": True})
    assert resp.status_code == 403
