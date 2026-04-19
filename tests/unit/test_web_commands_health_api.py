# -*- coding: utf-8 -*-
"""
Тесты для:
  /api/commands        — список Telegram-команд
  /api/v1/health       — версионированный health endpoint
  /api/health/lite     — быстрый liveness-check
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки (минимальные, повторяют паттерн соседних тест-файлов)
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Заглушка OpenClaw клиента."""

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
    """Заглушка userbot с методами translator/voice."""

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика TestClient
# ---------------------------------------------------------------------------


def _client() -> TestClient:
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
    app = WebApp(deps, port=18091, host="127.0.0.1")
    return TestClient(app.app)


# ---------------------------------------------------------------------------
# /api/commands
# ---------------------------------------------------------------------------


def test_commands_status_200() -> None:
    """GET /api/commands возвращает HTTP 200."""
    resp = _client().get("/api/commands")
    assert resp.status_code == 200


def test_commands_ok_true() -> None:
    """Поле ok должно быть True."""
    data = _client().get("/api/commands").json()
    assert data["ok"] is True


def test_commands_nonempty_list() -> None:
    """Список команд не пустой."""
    data = _client().get("/api/commands").json()
    assert isinstance(data["commands"], list)
    assert len(data["commands"]) >= 14  # минимум 14 команд в реализации


def test_commands_entries_schema() -> None:
    """Каждая запись содержит поля name и description."""
    commands = _client().get("/api/commands").json()["commands"]
    for entry in commands:
        assert "name" in entry, f"нет поля name: {entry}"
        assert "description" in entry, f"нет поля description: {entry}"


def test_commands_known_cmds_present() -> None:
    """Ключевые команды присутствуют в списке."""
    cmds = {e["name"] for e in _client().get("/api/commands").json()["commands"]}
    required = {"status", "model", "voice", "translator", "swarm", "help", "search"}
    missing = required - cmds
    assert not missing, f"отсутствуют команды: {missing}"


# ---------------------------------------------------------------------------
# /api/v1/health
# ---------------------------------------------------------------------------


def test_v1_health_status_200() -> None:
    """GET /api/v1/health возвращает HTTP 200."""
    resp = _client().get("/api/v1/health")
    assert resp.status_code == 200


def test_v1_health_ok_true() -> None:
    """Поле ok=True в нормальном режиме."""
    data = _client().get("/api/v1/health").json()
    assert data["ok"] is True


def test_v1_health_version_field() -> None:
    """Поле version равно '1'."""
    data = _client().get("/api/v1/health").json()
    assert data["version"] == "1"


def test_v1_health_required_fields() -> None:
    """Ответ содержит поля status, telegram, gateway, uptime_probe."""
    data = _client().get("/api/v1/health").json()
    for field in ("status", "telegram", "gateway", "uptime_probe"):
        assert field in data, f"отсутствует поле {field!r}"


def test_v1_health_uptime_probe_pass() -> None:
    """uptime_probe всегда равен 'pass' при успешном вызове."""
    data = _client().get("/api/v1/health").json()
    assert data["uptime_probe"] == "pass"


# ---------------------------------------------------------------------------
# /api/health/lite
# ---------------------------------------------------------------------------


def test_health_lite_status_200() -> None:
    """GET /api/health/lite возвращает HTTP 200."""
    resp = _client().get("/api/health/lite")
    assert resp.status_code == 200


def test_health_lite_ok_true() -> None:
    """Поле ok=True."""
    data = _client().get("/api/health/lite").json()
    assert data["ok"] is True


def test_health_lite_status_up() -> None:
    """Поле status='up'."""
    data = _client().get("/api/health/lite").json()
    assert data["status"] == "up"


def test_health_lite_contains_liveness_fields() -> None:
    """Ответ содержит ключи telegram_session_state и openclaw_auth_state."""
    data = _client().get("/api/health/lite").json()
    assert "telegram_session_state" in data
    assert "openclaw_auth_state" in data
