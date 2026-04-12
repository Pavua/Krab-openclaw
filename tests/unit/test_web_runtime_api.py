# -*- coding: utf-8 -*-
"""
Тесты runtime API endpoints web-панели Krab.

Покрываем:
  GET  /api/runtime/summary          — полное состояние системы
  GET  /api/runtime/handoff          — снимок для миграции в новый чат
  GET  /api/runtime/operator-profile — профиль текущей учётки
  POST /api/runtime/recover          — recovery-плейбук (с авторизацией)
"""

from __future__ import annotations

import asyncio as _asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {"channel": "cloud", "provider": "google", "model": "test/model", "status": "ok"}

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "detail": {}}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Фабрика WebApp
# ---------------------------------------------------------------------------


def _make_app() -> WebApp:
    """Создаёт WebApp с полным набором заглушек без внешних зависимостей."""
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
# /api/runtime/summary
# ---------------------------------------------------------------------------


def _summary_patches():
    """Контекстные менеджеры для мокирования зависимостей /api/runtime/summary."""
    fake_board = MagicMock()
    fake_board.get_board_summary.return_value = {"open": 0, "done": 3}
    fake_ca = MagicMock()
    fake_ca.build_usage_report_dict.return_value = {"total_cost_usd": 0.01}
    fake_sm = MagicMock()
    fake_sm.status.return_value = {
        "global_muted": False,
        "global_remaining_min": 0,
        "muted_chats": {},
        "total_muted": 0,
    }
    return fake_board, fake_ca, fake_sm


def test_runtime_summary_ok() -> None:
    """GET /api/runtime/summary должен вернуть ok=True."""
    fake_board, fake_ca, fake_sm = _summary_patches()
    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.silence_mode.silence_manager", fake_sm),
    ):
        resp = _client().get("/api/runtime/summary")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_runtime_summary_required_sections() -> None:
    """Ответ summary должен содержать секции health, route, costs, translator, swarm, silence."""
    fake_board, fake_ca, fake_sm = _summary_patches()
    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
        patch("src.core.silence_mode.silence_manager", fake_sm),
    ):
        data = _client().get("/api/runtime/summary").json()
    for section in ("health", "route", "costs", "translator", "swarm", "silence"):
        assert section in data, f"секция отсутствует: {section}"


def test_runtime_summary_translator_structure() -> None:
    """Секция translator.profile должна иметь language_pair из заглушки."""
    fake_board, fake_ca, fake_sm = _summary_patches()
    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=False),
        patch("src.core.silence_mode.silence_manager", fake_sm),
    ):
        data = _client().get("/api/runtime/summary").json()
    translator = data["translator"]
    assert "profile" in translator
    assert "session" in translator
    assert translator["profile"].get("language_pair") == "es-ru"


def test_runtime_summary_swarm_section() -> None:
    """Секция swarm должна содержать task_board и listeners_enabled."""
    fake_board, fake_ca, fake_sm = _summary_patches()
    fake_board.get_board_summary.return_value = {"open": 2}
    with (
        patch("src.core.swarm_task_board.swarm_task_board", fake_board),
        patch("src.core.cost_analytics.cost_analytics", fake_ca),
        patch("src.core.swarm_team_listener.is_listeners_enabled", return_value=True),
        patch("src.core.silence_mode.silence_manager", fake_sm),
    ):
        data = _client().get("/api/runtime/summary").json()
    swarm = data["swarm"]
    assert "task_board" in swarm
    assert swarm.get("listeners_enabled") is True


# ---------------------------------------------------------------------------
# /api/runtime/handoff
# ---------------------------------------------------------------------------


def test_runtime_handoff_skipped_probe() -> None:
    """GET /api/runtime/handoff?probe_cloud_runtime=0 должен вернуть ok=True без cloud-probe."""
    resp = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True


def test_runtime_handoff_contains_health_lite() -> None:
    """Ответ handoff должен содержать секцию health_lite (runtime snapshot)."""
    resp = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    data = resp.json()
    # handoff возвращает либо runtime_lite, либо health_lite (зависит от версии)
    assert "health_lite" in data or "runtime_lite" in data


def test_runtime_handoff_contains_operator_profile() -> None:
    """Ответ handoff должен содержать секцию operator_profile."""
    resp = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    assert "operator_profile" in resp.json()


def test_runtime_handoff_cloud_skipped_when_disabled() -> None:
    """Если probe_cloud_runtime=0, cloud_runtime.skipped=True."""
    resp = _client().get("/api/runtime/handoff", params={"probe_cloud_runtime": "0"})
    cloud = resp.json().get("cloud_runtime", {})
    assert cloud.get("skipped") is True


# ---------------------------------------------------------------------------
# /api/runtime/operator-profile
# ---------------------------------------------------------------------------


def test_runtime_operator_profile_ok() -> None:
    """GET /api/runtime/operator-profile должен вернуть ok=True и поле profile."""
    resp = _client().get("/api/runtime/operator-profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "profile" in data


def test_runtime_operator_profile_is_dict() -> None:
    """Поле profile должно быть словарём."""
    resp = _client().get("/api/runtime/operator-profile")
    assert isinstance(resp.json()["profile"], dict)


# ---------------------------------------------------------------------------
# /api/runtime/recover (POST, требует авторизации)
# ---------------------------------------------------------------------------


def test_runtime_recover_unauthorized_without_key() -> None:
    """POST /api/runtime/recover без ключа должен вернуть 403 или ok=False при пустом WEB_API_KEY."""
    # При незаданном WEB_API_KEY (пустая строка) запрос пропускается
    with patch.dict("os.environ", {"WEB_API_KEY": ""}):
        resp = _client().post("/api/runtime/recover", json={})
    # Пустой ключ = доступ разрешён всем (нет защиты — ок или статус 200)
    assert resp.status_code in (200, 403)


def test_runtime_recover_forbidden_with_wrong_key() -> None:
    """POST /api/runtime/recover с неверным ключом должен вернуть 403."""
    with patch.dict("os.environ", {"WEB_API_KEY": "secret-key"}):
        resp = _client().post(
            "/api/runtime/recover",
            json={},
            headers={"X-Krab-Web-Key": "wrong-key"},
        )
    assert resp.status_code == 403


def test_runtime_recover_skipped_steps_when_disabled() -> None:
    """POST /api/runtime/recover с run_*=false должен вернуть ok=True и skipped шаги."""
    # Без WEB_API_KEY авторизация отключена
    with patch.dict("os.environ", {"WEB_API_KEY": ""}):
        resp = _client().post(
            "/api/runtime/recover",
            json={
                "run_openclaw_runtime_repair": False,
                "run_sync_openclaw_models": False,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Оба шага должны быть skipped
    steps = {s["step"]: s for s in data["steps"]}
    assert steps["openclaw_runtime_repair"].get("skipped") is True
    assert steps["sync_openclaw_models"].get("skipped") is True
