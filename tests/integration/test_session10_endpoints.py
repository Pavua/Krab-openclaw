# -*- coding: utf-8 -*-
"""
Интеграционные тесты для Session 10 aggregated endpoints.

Покрываем:
- GET /api/session10/summary — единый aggregated view для V4 Hub
  (session_info, memory_validator, memory_archive, new_commands,
   dedicated_chrome, auto_restart, observability, known_issues).

Тесты защищены от отсутствия опциональных модулей (memory_validator и т.д.) —
endpoint должен возвращать defaults, не 500.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки зависимостей (копируют паттерн test_web_app_dashboard_endpoints.py)
# ---------------------------------------------------------------------------


class _DummyRouter:
    """Минимальный роутер-заглушка."""

    def get_model_info(self) -> dict:
        return {}

    def health_check(self):  # pragma: no cover - AsyncMock используется ниже
        return None


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "google/gemini-3-pro-preview",
            "status": "ok",
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free"}

    async def get_cloud_runtime_check(self) -> dict:
        return {"ok": True}

    async def health_check(self) -> bool:
        return True


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"enabled": False, "src_lang": "ru", "tgt_lang": "es"}

    def get_translator_session_state(self) -> dict:
        return {"active": False, "session_id": None}


def _build_client(*, watchdog: Any = None) -> TestClient:
    """Создаёт TestClient с набором зависимостей."""
    router = MagicMock()
    router.health_check = AsyncMock(return_value={"status": "healthy"})
    router.task_queue = None
    router.cost_analytics = None
    router.cost_engine = None

    deps = {
        "router": router,
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "health_service": None,
        "provisioning_service": None,
        "ai_runtime": None,
        "reaction_engine": None,
        "voice_gateway_client": _FakeHealthClient(),
        "krab_ear_client": _FakeHealthClient(),
        "perceptor": None,
        "watchdog": watchdog,
        "queue": None,
        "kraab_userbot": _FakeKraab(),
    }
    app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app)


@pytest.fixture
def client() -> TestClient:
    return _build_client()


# ---------------------------------------------------------------------------
# Contract tests (требуются заданием)
# ---------------------------------------------------------------------------


def test_session10_summary_returns_200(client: TestClient) -> None:
    """GET /api/session10/summary возвращает 200 и нужные top-level ключи."""
    r = client.get("/api/session10/summary")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "session_info" in d
    assert "memory_validator" in d
    assert "memory_archive" in d
    assert "new_commands" in d
    assert isinstance(d["new_commands"], list)


def test_session10_summary_new_commands_list(client: TestClient) -> None:
    """new_commands содержит дебютные команды Session 10."""
    r = client.get("/api/session10/summary")
    names = [c["name"] for c in r.json()["new_commands"]]
    assert "!confirm" in names
    assert "!reset" in names


def test_session10_summary_has_observability(client: TestClient) -> None:
    """Секция observability содержит правильные флаги + порог stagnation."""
    r = client.get("/api/session10/summary")
    obs = r.json().get("observability", {})
    assert obs.get("correlation_id_active") is True
    assert obs.get("tool_indicator_enabled") is True
    assert isinstance(obs.get("stagnation_threshold_sec"), int)


# ---------------------------------------------------------------------------
# Дополнительное покрытие (session_info / defaults / watchdog)
# ---------------------------------------------------------------------------


def test_session10_summary_session_info_has_static_fields(client: TestClient) -> None:
    """session_info содержит статичные поля name/date/status/new_tests_count."""
    d = client.get("/api/session10/summary").json()
    info = d["session_info"]
    assert info["name"] == "Session 10"
    assert info["date"] == "2026-04-17"
    assert info["status"] == "closed"
    assert isinstance(info["new_tests_count"], int)
    assert info["new_tests_count"] >= 0


def test_session10_summary_memory_validator_defaults(client: TestClient) -> None:
    """memory_validator возвращает defaults даже когда модуль отсутствует."""
    d = client.get("/api/session10/summary").json()
    mv = d["memory_validator"]
    # Все ключи присутствуют, значения — числа либо bool
    for k in (
        "safe_total",
        "injection_blocked_total",
        "confirmed_total",
        "confirm_failed_total",
        "pending_count",
    ):
        assert k in mv
        assert isinstance(mv[k], int)
    assert isinstance(mv.get("enabled"), bool)


def test_session10_summary_memory_archive_shape(client: TestClient) -> None:
    """memory_archive содержит ожидаемые поля с корректными типами."""
    d = client.get("/api/session10/summary").json()
    ar = d["memory_archive"]
    assert isinstance(ar["exists"], bool)
    assert isinstance(ar["size_bytes"], int)
    assert isinstance(ar["size_mb"], (int, float))
    assert isinstance(ar["message_count"], int)
    assert isinstance(ar["chats_count"], int)
    assert isinstance(ar["chunks_count"], int)
    assert isinstance(ar["indexer_state"], str)


def test_session10_summary_dedicated_chrome_shape(client: TestClient) -> None:
    """dedicated_chrome имеет boolean enabled/running и int port."""
    d = client.get("/api/session10/summary").json()
    dc = d["dedicated_chrome"]
    assert isinstance(dc["enabled"], bool)
    assert isinstance(dc["running"], bool)
    assert isinstance(dc["port"], int)
    assert dc["port"] > 0


def test_session10_summary_auto_restart_empty_deps(client: TestClient) -> None:
    """При отсутствии watchdog deps — auto_restart disabled + empty list."""
    d = client.get("/api/session10/summary").json()
    ar = d["auto_restart"]
    assert ar["enabled"] is False
    assert ar["services_tracked"] == []
    assert ar["total_attempts_last_hour"] == 0


def test_session10_summary_auto_restart_with_watchdog() -> None:
    """Если watchdog в deps — auto_restart enabled и services_tracked заполнен."""
    watchdog = MagicMock()
    watchdog.last_recovery_attempt = {
        "openclaw": {"ts": 0, "ok": True},
        "lmstudio": {"ts": 0, "ok": True},
    }
    c = _build_client(watchdog=watchdog)
    d = c.get("/api/session10/summary").json()
    ar = d["auto_restart"]
    assert ar["enabled"] is True
    assert "openclaw" in ar["services_tracked"]
    assert "lmstudio" in ar["services_tracked"]


def test_session10_summary_known_issues_is_list(client: TestClient) -> None:
    """known_issues — всегда список (по умолчанию пустой)."""
    d = client.get("/api/session10/summary").json()
    assert isinstance(d["known_issues"], list)


def test_session10_summary_generated_at_is_epoch(client: TestClient) -> None:
    """generated_at — int (unix epoch)."""
    d = client.get("/api/session10/summary").json()
    assert isinstance(d["generated_at"], int)
    assert d["generated_at"] > 0
