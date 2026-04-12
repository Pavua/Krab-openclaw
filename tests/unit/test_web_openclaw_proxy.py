# -*- coding: utf-8 -*-
"""
Тесты OpenClaw proxy endpoint'ов owner-панели Krab.

Покрываем:
  POST /api/model/switch
  GET  /api/openclaw/model-routing/status
  GET  /api/openclaw/cron/status
  GET  /api/openclaw/cron/jobs
  GET  /api/openclaw/channels/status
  GET  /api/openclaw/browser-smoke
  GET  /api/openclaw/report
  GET  /api/openclaw/deep-check
  GET  /api/openclaw/cloud
  GET  /api/openclaw/runtime-config
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------

WEB_KEY = "test-secret"


class _FakeOpenClaw:
    """Минимальный OpenClaw клиент — только то, что нужно для тестируемых эндпоинтов."""

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

    async def get_health_report(self) -> dict:
        return {"status": "ok", "uptime": 42}

    async def get_deep_health_report(self) -> dict:
        return {"status": "ok", "tools_smoke": "pass"}

    async def get_cloud_provider_diagnostics(self, providers=None) -> dict:
        return {"ok": True, "providers": ["google"]}


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
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _FakeModelManager:
    active_model_id: str = "google/gemini-initial"

    def format_status(self) -> str:
        return f"{self.active_model_id} (ok)"

    def set_model(self, model_id: str) -> None:
        self.active_model_id = model_id

    def set_provider(self, provider: str) -> None:
        self.active_model_id = f"provider:{provider}"


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
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
        "kraab_userbot": _FakeKraab(),
    }
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# POST /api/model/switch
# ---------------------------------------------------------------------------


def test_model_switch_sets_specific_model() -> None:
    """POST /api/model/switch с конкретным model_id должен вернуть ok=True и нужную модель."""
    fake_mm = _FakeModelManager()
    with (
        patch("src.modules.web_app.model_manager", fake_mm, create=True),
        patch("src.model_manager.model_manager", fake_mm, create=True),
    ):
        resp = _client().post(
            "/api/model/switch",
            json={"model": "google/gemini-3-pro-preview"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["model"] == "google/gemini-3-pro-preview"


def test_model_switch_missing_model_returns_error() -> None:
    """POST /api/model/switch без поля model должен вернуть ok=False."""
    resp = _client().post(
        "/api/model/switch",
        json={},
        headers={"X-Krab-Web-Key": WEB_KEY},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


def test_model_switch_auto_provider() -> None:
    """POST /api/model/switch с model='auto' должен вернуть ok=True."""
    fake_mm = _FakeModelManager()
    with (
        patch("src.modules.web_app.model_manager", fake_mm, create=True),
        patch("src.model_manager.model_manager", fake_mm, create=True),
    ):
        resp = _client().post(
            "/api/model/switch",
            json={"model": "auto"},
            headers={"X-Krab-Web-Key": WEB_KEY},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# GET /api/openclaw/model-routing/status
# ---------------------------------------------------------------------------


def test_model_routing_status_ok() -> None:
    """GET /api/openclaw/model-routing/status должен вернуть ok=True и ключ routing."""
    resp = _client().get("/api/openclaw/model-routing/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "routing" in data


def test_model_routing_status_has_primary() -> None:
    """Объект routing должен содержать поле current_primary с id модели."""
    resp = _client().get("/api/openclaw/model-routing/status")
    routing = resp.json()["routing"]
    # runtime хранит текущую первичную модель в current_primary
    assert "current_primary" in routing
    assert isinstance(routing["current_primary"], str)


# ---------------------------------------------------------------------------
# GET /api/openclaw/cron/status
# ---------------------------------------------------------------------------


def test_cron_status_timeout_returns_ok_false() -> None:
    """Если _collect_openclaw_cron_snapshot истекает по таймауту — ok=False."""
    import asyncio

    app = _make_app()
    client = TestClient(app.app)

    async def _slow(*args, **kwargs):
        await asyncio.sleep(999)  # симулируем зависание

    with patch.object(app, "_collect_openclaw_cron_snapshot", side_effect=_slow):
        resp = client.get("/api/openclaw/cron/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data["error"].lower() or "openclaw" in data["error"].lower()


def test_cron_status_success() -> None:
    """Если snapshot возвращает ok=True — endpoint отдаёт его напрямую."""
    snapshot = {"ok": True, "summary": {"total": 3}, "jobs": [{"id": "j1"}]}
    app = _make_app()
    client = TestClient(app.app)

    async def _fast(*args, **kwargs):
        return snapshot

    with patch.object(app, "_collect_openclaw_cron_snapshot", side_effect=_fast):
        resp = client.get("/api/openclaw/cron/status")

    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["jobs"][0]["id"] == "j1"


# ---------------------------------------------------------------------------
# GET /api/openclaw/cron/jobs
# ---------------------------------------------------------------------------


def test_cron_jobs_returns_jobs_list() -> None:
    """GET /api/openclaw/cron/jobs возвращает список jobs и summary."""
    snapshot = {
        "ok": True,
        "summary": {"total": 2},
        "jobs": [{"id": "a"}, {"id": "b"}],
    }
    app = _make_app()
    client = TestClient(app.app)

    async def _fast(*args, **kwargs):
        return snapshot

    with patch.object(app, "_collect_openclaw_cron_snapshot", side_effect=_fast):
        resp = client.get("/api/openclaw/cron/jobs")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["jobs"], list)
    assert len(data["jobs"]) == 2


def test_cron_jobs_timeout_returns_error() -> None:
    """GET /api/openclaw/cron/jobs при таймауте возвращает ok=False."""
    import asyncio

    app = _make_app()
    client = TestClient(app.app)

    async def _slow(*args, **kwargs):
        await asyncio.sleep(999)

    with patch.object(app, "_collect_openclaw_cron_snapshot", side_effect=_slow):
        resp = client.get("/api/openclaw/cron/jobs")

    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/openclaw/channels/status
# ---------------------------------------------------------------------------


def test_channels_status_subprocess_timeout() -> None:
    """При зависании subprocess channels/status — endpoint возвращает ok=False."""
    import asyncio

    app = _make_app()
    client = TestClient(app.app)

    # Мокаем asyncio.create_subprocess_exec чтобы вернуть вечно ожидающий процесс
    async def _fake_proc(*args, **kwargs):
        mock_proc = AsyncMock()
        mock_proc.returncode = None

        async def _communicate():
            await asyncio.sleep(999)

        mock_proc.communicate = _communicate
        mock_proc.terminate = MagicMock()
        return mock_proc

    with patch("asyncio.create_subprocess_exec", side_effect=_fake_proc):
        resp = client.get("/api/openclaw/channels/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


def test_channels_status_subprocess_error() -> None:
    """Если subprocess бросает исключение — endpoint возвращает ok=False и error."""
    app = _make_app()
    client = TestClient(app.app)

    async def _raise(*args, **kwargs):
        raise FileNotFoundError("openclaw not found")

    with patch("asyncio.create_subprocess_exec", side_effect=_raise):
        resp = client.get("/api/openclaw/channels/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "error" in data


# ---------------------------------------------------------------------------
# GET /api/openclaw/browser-smoke
# ---------------------------------------------------------------------------


def test_browser_smoke_success() -> None:
    """Если _collect_openclaw_browser_smoke_report отвечает — available=True."""
    report = {"browser_smoke": {"ok": True, "detail": "chrome attached"}}
    app = _make_app()
    client = TestClient(app.app)

    async def _fast(*args, **kwargs):
        return report

    with patch.object(app, "_collect_openclaw_browser_smoke_report", side_effect=_fast):
        resp = client.get("/api/openclaw/browser-smoke")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert "report" in data


def test_browser_smoke_timeout() -> None:
    """Если _collect_openclaw_browser_smoke_report зависает — available=False."""
    import asyncio

    app = _make_app()
    client = TestClient(app.app)

    async def _slow(*args, **kwargs):
        await asyncio.sleep(999)

    with patch.object(app, "_collect_openclaw_browser_smoke_report", side_effect=_slow):
        resp = client.get("/api/openclaw/browser-smoke")

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False
    assert "timeout" in data.get("error", "").lower() or "openclaw" in data.get("error", "").lower()


# ---------------------------------------------------------------------------
# GET /api/openclaw/report
# ---------------------------------------------------------------------------


def test_openclaw_report_available() -> None:
    """GET /api/openclaw/report с поддерживающим клиентом возвращает available=True."""
    resp = _client().get("/api/openclaw/report")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert "report" in data


def test_openclaw_report_no_client() -> None:
    """GET /api/openclaw/report без openclaw_client возвращает available=False."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": None,
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
    client = TestClient(app.app)
    resp = client.get("/api/openclaw/report")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


# ---------------------------------------------------------------------------
# GET /api/openclaw/cloud
# ---------------------------------------------------------------------------


def test_openclaw_cloud_available() -> None:
    """GET /api/openclaw/cloud с поддерживающим клиентом возвращает available=True."""
    resp = _client().get("/api/openclaw/cloud")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True


def test_openclaw_cloud_accepts_providers_param() -> None:
    """GET /api/openclaw/cloud?providers= принимается без ошибок."""
    resp = _client().get("/api/openclaw/cloud?providers=google")
    assert resp.status_code == 200
    assert "available" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/openclaw/runtime-config
# ---------------------------------------------------------------------------


def test_runtime_config_returns_dict() -> None:
    """GET /api/openclaw/runtime-config возвращает словарь с ключами конфигурации."""
    resp = _client().get("/api/openclaw/runtime-config")
    assert resp.status_code == 200
    data = resp.json()
    # должны присутствовать базовые ключи runtime конфига
    assert isinstance(data, dict)
    # проверяем что хотя бы один ключ присутствует
    assert len(data) > 0
