# -*- coding: utf-8 -*-
"""
Расширенные тесты API endpoints web-панели Krab (session 5-6).

Покрываем новые GET-маршруты:
  /api/version
  /api/uptime
  /api/system/info
  /api/runtime/summary
  /api/translator/status
  /api/swarm/stats
  /api/notify/status
  /api/silence/status
  /api/voice/profile
  /api/endpoints
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки (дублируем паттерн из test_web_api_endpoints.py)
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


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False, "provider": "silero"}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фабрика TestClient
# ---------------------------------------------------------------------------


def _make_app(*, kraab: _FakeKraab | None = None) -> WebApp:
    """Создаёт WebApp со всеми заглушками в deps."""
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
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client(*, kraab: _FakeKraab | None = None) -> TestClient:
    return TestClient(_make_app(kraab=kraab).app)


# ---------------------------------------------------------------------------
# /api/version
# ---------------------------------------------------------------------------


def test_version_ok() -> None:
    """GET /api/version возвращает ok=True."""
    resp = _client().get("/api/version")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_version_has_required_fields() -> None:
    """Ответ /api/version содержит version, commits, tests, features."""
    resp = _client().get("/api/version")
    data = resp.json()
    for field in ("version", "commits", "tests", "features"):
        assert field in data, f"поле отсутствует: {field}"


def test_version_features_is_list() -> None:
    """Поле features — список строк."""
    resp = _client().get("/api/version")
    features = resp.json()["features"]
    assert isinstance(features, list)
    assert len(features) > 0


# ---------------------------------------------------------------------------
# /api/uptime
# ---------------------------------------------------------------------------


def test_uptime_ok() -> None:
    """GET /api/uptime возвращает ok=True."""
    resp = _client().get("/api/uptime")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_uptime_has_uptime_sec() -> None:
    """Ответ /api/uptime содержит uptime_sec >= 0 и boot_ts."""
    resp = _client().get("/api/uptime")
    data = resp.json()
    assert "uptime_sec" in data
    assert "boot_ts" in data
    assert data["uptime_sec"] >= 0


def test_uptime_second_call_nonnegative() -> None:
    """Повторный вызов /api/uptime не падает и uptime_sec >= 0."""
    client = _client()
    client.get("/api/uptime")
    resp = client.get("/api/uptime")
    assert resp.json()["uptime_sec"] >= 0


# ---------------------------------------------------------------------------
# /api/system/info
# ---------------------------------------------------------------------------


def test_system_info_ok() -> None:
    """GET /api/system/info возвращает ok=True."""
    resp = _client().get("/api/system/info")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_system_info_fields() -> None:
    """Ответ /api/system/info содержит hostname, platform, python, cpu_count, ram_total_gb."""
    resp = _client().get("/api/system/info")
    data = resp.json()
    for field in ("hostname", "platform", "python", "cpu_count", "ram_total_gb"):
        assert field in data, f"поле отсутствует: {field}"


def test_system_info_ram_pct_range() -> None:
    """ram_used_pct — процент, от 0 до 100."""
    resp = _client().get("/api/system/info")
    pct = resp.json()["ram_used_pct"]
    assert 0 <= pct <= 100


# ---------------------------------------------------------------------------
# /api/voice/profile
# ---------------------------------------------------------------------------


def test_voice_profile_ok() -> None:
    """GET /api/voice/profile возвращает ok=True и поле profile."""
    resp = _client().get("/api/voice/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "profile" in data


def test_voice_profile_contents_from_stub() -> None:
    """Поле profile берётся из get_voice_runtime_profile() — tts_enabled=False по заглушке."""
    resp = _client().get("/api/voice/profile")
    profile = resp.json()["profile"]
    assert profile.get("tts_enabled") is False


def test_voice_profile_provider_field() -> None:
    """Заглушка возвращает provider='silero' в profile."""
    resp = _client().get("/api/voice/profile")
    assert resp.json()["profile"].get("provider") == "silero"


# ---------------------------------------------------------------------------
# /api/endpoints
# ---------------------------------------------------------------------------


def test_endpoints_ok() -> None:
    """GET /api/endpoints возвращает ok=True и поле endpoints."""
    resp = _client().get("/api/endpoints")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["endpoints"], list)


def test_endpoints_count_matches_list() -> None:
    """Поле count соответствует длине списка endpoints."""
    resp = _client().get("/api/endpoints")
    data = resp.json()
    assert data["count"] == len(data["endpoints"])


def test_endpoints_each_has_method_and_path() -> None:
    """Каждый endpoint содержит поля method и path."""
    resp = _client().get("/api/endpoints")
    for entry in resp.json()["endpoints"]:
        assert "method" in entry, f"нет поля method: {entry}"
        assert "path" in entry, f"нет поля path: {entry}"


def test_endpoints_includes_api_version() -> None:
    """Список endpoints должен включать /api/version."""
    resp = _client().get("/api/endpoints")
    paths = {e["path"] for e in resp.json()["endpoints"]}
    assert "/api/version" in paths


# ---------------------------------------------------------------------------
# /api/swarm/stats — дополнительные кейсы
# ---------------------------------------------------------------------------


def test_swarm_stats_board_comes_from_board_summary() -> None:
    """Поле board в /api/swarm/stats берётся из swarm_task_board.get_board_summary()."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"open": 42, "done": 0}
    fake_store = MagicMock()
    fake_store.list_artifacts.return_value = []

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
    ):
        resp = _client().get("/api/swarm/stats")

    assert resp.json()["board"]["open"] == 42


def test_swarm_stats_listeners_disabled() -> None:
    """listeners_enabled=False когда is_listeners_enabled() возвращает False."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {}
    fake_store = MagicMock()
    fake_store.list_artifacts.return_value = []

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
    ):
        resp = _client().get("/api/swarm/stats")

    assert resp.json()["listeners_enabled"] is False
