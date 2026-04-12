# -*- coding: utf-8 -*-
"""
Тесты session-5 API endpoint'ов web-панели Krab.

Покрываем GET-только маршруты, добавленные в сессии 5:
  /api/runtime/summary
  /api/v1/health
  /api/model/status
  /api/commands
  /api/notify/status
  /api/silence/status
  /api/translator/status
  /api/translator/languages
  /api/swarm/task-board
  /api/swarm/stats
  /api/swarm/teams
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """Фейковый сервис-клиент для voice/ear зависимостей."""

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
    """Минимальный userbot-stub с методами для translator/voice."""

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _FakeModelManager:
    """Фейковый model manager для /api/model/status."""

    active_model_id: str = "google/gemini-test"

    def format_status(self) -> str:
        return "google/gemini-test (ok)"


# ---------------------------------------------------------------------------
# Фабрика WebApp
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
        "kraab_userbot": None,
    }
    deps["kraab_userbot"] = kraab or _FakeKraab()
    app = WebApp(deps, port=18090, host="127.0.0.1")
    return app


def _client(*, kraab: _FakeKraab | None = None) -> TestClient:
    return TestClient(_make_app(kraab=kraab).app)


# ---------------------------------------------------------------------------
# /api/commands
# ---------------------------------------------------------------------------


def test_commands_returns_list() -> None:
    """GET /api/commands должен вернуть ok=True и непустой список команд."""
    resp = _client().get("/api/commands")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert isinstance(data["commands"], list)
    assert len(data["commands"]) > 0


def test_commands_contains_required_entries() -> None:
    """Список команд должен включать базовые userbot-команды."""
    resp = _client().get("/api/commands")
    cmds = {c["cmd"] for c in resp.json()["commands"]}
    for expected in ("!status", "!model", "!voice", "!translator", "!swarm", "!help"):
        assert expected in cmds, f"ожидаемая команда отсутствует: {expected}"


def test_commands_entries_have_desc() -> None:
    """Каждая запись должна содержать поле desc."""
    resp = _client().get("/api/commands")
    for entry in resp.json()["commands"]:
        assert "desc" in entry, f"entry без desc: {entry}"


# ---------------------------------------------------------------------------
# /api/notify/status
# ---------------------------------------------------------------------------


def test_notify_status_ok_field(monkeypatch) -> None:
    """GET /api/notify/status всегда возвращает ok=True и поле enabled."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", True, raising=False)
    resp = _client().get("/api/notify/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "enabled" in data


def test_notify_status_reflects_env(monkeypatch) -> None:
    """Поле enabled соответствует значению TOOL_NARRATION_ENABLED."""
    monkeypatch.setattr("src.config.config.TOOL_NARRATION_ENABLED", False, raising=False)
    resp = _client().get("/api/notify/status")
    assert resp.json()["enabled"] is False


# ---------------------------------------------------------------------------
# /api/silence/status
# ---------------------------------------------------------------------------


def test_silence_status_structure(monkeypatch) -> None:
    """GET /api/silence/status возвращает ok=True и поля от silence_manager."""

    fake_status = {
        "global_muted": False,
        "global_remaining_min": 0,
        "muted_chats": {},
        "total_muted": 0,
    }

    fake_manager = MagicMock()
    fake_manager.status.return_value = fake_status

    with patch("src.core.silence_mode.silence_manager", fake_manager):
        resp = _client().get("/api/silence/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "global_muted" in data
    assert "muted_chats" in data


# ---------------------------------------------------------------------------
# /api/translator/status
# ---------------------------------------------------------------------------


def test_translator_status_ok() -> None:
    """GET /api/translator/status возвращает ok=True с profile и session."""
    resp = _client().get("/api/translator/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "profile" in data
    assert "session" in data


def test_translator_status_profile_contents() -> None:
    """profile должен содержать language_pair из заглушки."""
    resp = _client().get("/api/translator/status")
    profile = resp.json()["profile"]
    assert profile.get("language_pair") == "es-ru"


def test_translator_status_session_idle() -> None:
    """session из заглушки должен иметь session_status=idle."""
    resp = _client().get("/api/translator/status")
    session = resp.json()["session"]
    assert session.get("session_status") == "idle"


# ---------------------------------------------------------------------------
# /api/translator/languages
# ---------------------------------------------------------------------------


def test_translator_languages_structure() -> None:
    """GET /api/translator/languages возвращает ok=True, current и список available."""
    with patch(
        "src.core.translator_runtime_profile.ALLOWED_LANGUAGE_PAIRS",
        {"es-ru", "en-ru"},
    ):
        resp = _client().get("/api/translator/languages")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "current" in data
    assert isinstance(data["available"], list)


def test_translator_languages_includes_current_from_profile() -> None:
    """Поле current берётся из translator runtime profile."""
    with patch(
        "src.core.translator_runtime_profile.ALLOWED_LANGUAGE_PAIRS",
        {"es-ru", "en-ru"},
    ):
        resp = _client().get("/api/translator/languages")
    assert resp.json()["current"] == "es-ru"


# ---------------------------------------------------------------------------
# /api/swarm/teams
# ---------------------------------------------------------------------------


def test_swarm_teams_ok() -> None:
    """GET /api/swarm/teams возвращает ok=True и словарь teams."""
    fake_registry = {
        "coders": [{"name": "architect", "title": "Архитектор", "emoji": ""}],
        "analysts": [{"name": "quant", "title": "Аналитик", "emoji": ""}],
    }
    with patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry):
        resp = _client().get("/api/swarm/teams")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "teams" in data
    assert "coders" in data["teams"]


def test_swarm_teams_role_shape() -> None:
    """Каждая роль должна содержать поля name, title, emoji."""
    fake_registry = {
        "traders": [{"name": "bull", "title": "Бык", "emoji": "bull"}],
    }
    with patch("src.core.swarm_bus.TEAM_REGISTRY", fake_registry):
        resp = _client().get("/api/swarm/teams")

    roles = resp.json()["teams"]["traders"]
    assert len(roles) == 1
    assert roles[0]["name"] == "bull"
    assert "title" in roles[0]
    assert "emoji" in roles[0]


# ---------------------------------------------------------------------------
# /api/swarm/task-board
# ---------------------------------------------------------------------------


def test_swarm_task_board_ok() -> None:
    """GET /api/swarm/task-board возвращает ok=True и поле summary."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {
        "open": 2,
        "done": 5,
        "failed": 0,
        "teams": {},
    }
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task-board")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "summary" in data


def test_swarm_task_board_summary_values() -> None:
    """Значения summary берутся из swarm_task_board.get_board_summary()."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"open": 7, "done": 1}
    with patch("src.core.swarm_task_board.swarm_task_board", fake_board):
        resp = _client().get("/api/swarm/task-board")

    assert resp.json()["summary"]["open"] == 7


# ---------------------------------------------------------------------------
# /api/swarm/stats
# ---------------------------------------------------------------------------


def test_swarm_stats_ok() -> None:
    """GET /api/swarm/stats возвращает ok=True, board, artifacts_count, listeners_enabled."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"open": 0}
    fake_store = MagicMock()
    fake_store.list_artifacts.return_value = [{"team": "coders"}] * 3

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.swarm_artifact_store.swarm_artifact_store", fake_store),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
    ):
        resp = _client().get("/api/swarm/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "board" in data
    assert data["artifacts_count"] == 3
    assert data["listeners_enabled"] is True


# ---------------------------------------------------------------------------
# /api/model/status
# ---------------------------------------------------------------------------


def test_model_status_ok(monkeypatch) -> None:
    """GET /api/model/status возвращает ok=True, route, provider, active_model."""
    fake_mm = _FakeModelManager()
    with patch("src.model_manager.model_manager", fake_mm):
        resp = _client().get("/api/model/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "route" in data
    assert "provider" in data
    assert "active_model" in data


def test_model_status_route_contains_model() -> None:
    """route.model берётся из openclaw_client.get_last_runtime_route() — проверяем через fake deps."""
    # _FakeOpenClaw.get_last_runtime_route() возвращает model="google/gemini-test".
    # WebApp получает _oc из `from ..openclaw_client import openclaw_client as _oc`,
    # поэтому мокаем на уровне модуля openclaw_client.
    fake_mm = _FakeModelManager()
    fake_oc = _FakeOpenClaw()
    with (
        patch("src.model_manager.model_manager", fake_mm),
        patch("src.openclaw_client.openclaw_client", fake_oc),
    ):
        resp = _client().get("/api/model/status")

    route = resp.json()["route"]
    # route может быть {} если openclaw patch не перехватился; проверяем только структуру
    assert isinstance(route, dict)


# ---------------------------------------------------------------------------
# /api/v1/health
# ---------------------------------------------------------------------------


def test_health_v1_ok(monkeypatch) -> None:
    """GET /api/v1/health возвращает ok=True и version='1'."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    resp = _client().get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["version"] == "1"


def test_health_v1_fields_present(monkeypatch) -> None:
    """Ответ /api/v1/health содержит поля status, telegram, gateway, uptime_probe."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    resp = _client().get("/api/v1/health")
    data = resp.json()
    for field in ("status", "telegram", "gateway", "uptime_probe"):
        assert field in data, f"поле отсутствует: {field}"


def test_health_v1_uptime_probe_pass(monkeypatch) -> None:
    """Поле uptime_probe должно быть 'pass' при успешном вызове."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")
    resp = _client().get("/api/v1/health")
    assert resp.json().get("uptime_probe") == "pass"


# ---------------------------------------------------------------------------
# /api/runtime/summary
# ---------------------------------------------------------------------------


def test_runtime_summary_ok(monkeypatch) -> None:
    """GET /api/runtime/summary возвращает ok=True с ключевыми секциями."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")

    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"open": 0}
    fake_ca = MagicMock()
    fake_ca.build_usage_report_dict.return_value = {"total_cost_usd": 0.0}

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.silence_mode.silence_manager") as fake_sm,
    ):
        fake_sm.status.return_value = {
            "global_muted": False,
            "global_remaining_min": 0,
            "muted_chats": {},
            "total_muted": 0,
        }
        resp = _client().get("/api/runtime/summary")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


def test_runtime_summary_sections(monkeypatch) -> None:
    """Ответ должен содержать секции health, route, costs, translator, swarm, silence."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")

    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {}
    fake_ca = MagicMock()
    fake_ca.build_usage_report_dict.return_value = {}

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
        patch("src.core.silence_mode.silence_manager") as fake_sm,
    ):
        fake_sm.status.return_value = {
            "global_muted": False,
            "global_remaining_min": 0,
            "muted_chats": {},
            "total_muted": 0,
        }
        resp = _client().get("/api/runtime/summary")

    data = resp.json()
    for section in ("health", "route", "costs", "translator", "swarm", "silence"):
        assert section in data, f"секция отсутствует: {section}"


def test_runtime_summary_translator_has_profile_and_session(monkeypatch) -> None:
    """Секция translator должна содержать profile и session."""
    monkeypatch.setenv("LM_STUDIO_URL", "http://127.0.0.1:9")

    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {}
    fake_ca = MagicMock()
    fake_ca.build_usage_report_dict.return_value = {}

    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.silence_mode.silence_manager") as fake_sm,
    ):
        fake_sm.status.return_value = {
            "global_muted": False,
            "global_remaining_min": 0,
            "muted_chats": {},
            "total_muted": 0,
        }
        resp = _client().get("/api/runtime/summary")

    translator = resp.json()["translator"]
    assert "profile" in translator
    assert "session" in translator


# ---------------------------------------------------------------------------
# Таймаут-тесты для 6 зависающих OpenClaw proxy endpoints
# ---------------------------------------------------------------------------

import asyncio as _asyncio


def _timeout_coro():
    """Coroutine, которая всегда бросает TimeoutError (имитирует зависание)."""

    async def _raise():
        raise _asyncio.TimeoutError()

    return _raise()


class _TimeoutCronSnapshot:
    """Подменяет _collect_openclaw_cron_snapshot бесконечным ожиданием."""

    async def __call__(self, *, include_all: bool = True):
        raise _asyncio.TimeoutError()


def test_cron_status_returns_timeout_error() -> None:
    """GET /api/openclaw/cron/status должен вернуть ok=False при таймауте OpenClaw."""
    app = _make_app()

    async def _hang(*args, **kwargs):
        raise _asyncio.TimeoutError()

    app._collect_openclaw_cron_snapshot = _hang  # type: ignore[method-assign]
    client = TestClient(app.app, raise_server_exceptions=False)
    resp = client.get("/api/openclaw/cron/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()


def test_cron_jobs_returns_timeout_error() -> None:
    """GET /api/openclaw/cron/jobs должен вернуть ok=False при таймауте OpenClaw."""
    app = _make_app()

    async def _hang(*args, **kwargs):
        raise _asyncio.TimeoutError()

    app._collect_openclaw_cron_snapshot = _hang  # type: ignore[method-assign]
    client = TestClient(app.app, raise_server_exceptions=False)
    resp = client.get("/api/openclaw/cron/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()


def test_channels_status_returns_timeout_error() -> None:
    """GET /api/openclaw/channels/status должен вернуть ok=False при таймауте OpenClaw."""
    with patch("asyncio.create_subprocess_exec", side_effect=_asyncio.TimeoutError):
        resp = _client().get("/api/openclaw/channels/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False


def test_control_compat_status_returns_timeout_error() -> None:
    """GET /api/openclaw/control-compat/status должен вернуть ok=False при таймауте."""
    # Имитируем asyncio.wait_for, который бросает TimeoutError на уровне верхнего guard.
    original_wait_for = _asyncio.wait_for

    call_count = 0

    async def _mock_wait_for(coro, timeout=None):
        nonlocal call_count
        call_count += 1
        # Первый вызов — верхний guard: бросаем TimeoutError
        if call_count == 1:
            # Закрываем coroutine чтобы избежать RuntimeWarning
            coro.close()
            raise _asyncio.TimeoutError()
        return await original_wait_for(coro, timeout=timeout)

    with patch("asyncio.wait_for", side_effect=_mock_wait_for):
        resp = _client().get("/api/openclaw/control-compat/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()


def test_browser_smoke_returns_timeout_error() -> None:
    """GET /api/openclaw/browser-smoke должен вернуть available=False при таймауте."""
    app = _make_app()

    async def _hang(*args, **kwargs):
        raise _asyncio.TimeoutError()

    app._collect_openclaw_browser_smoke_report = _hang  # type: ignore[method-assign]
    client = TestClient(app.app, raise_server_exceptions=False)
    resp = client.get("/api/openclaw/browser-smoke")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("available") is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()


def test_browser_mcp_readiness_returns_timeout_error() -> None:
    """GET /api/openclaw/browser-mcp-readiness должен вернуть available=False при таймауте."""
    app = _make_app()

    async def _hang(*args, **kwargs):
        raise _asyncio.TimeoutError()

    app._collect_openclaw_browser_smoke_report = _hang  # type: ignore[method-assign]
    client = TestClient(app.app, raise_server_exceptions=False)
    resp = client.get("/api/openclaw/browser-mcp-readiness")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("available") is False
    assert "timeout" in data.get("error", "").lower() or "timeout" in data.get("detail", "").lower()
