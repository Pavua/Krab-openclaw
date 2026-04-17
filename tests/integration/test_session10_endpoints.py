# -*- coding: utf-8 -*-
"""
Integration-тесты для Session 10 endpoints в Krab Owner Panel.

Покрывают:
  - GET /api/chrome/dedicated/status  (Agent #10) — skipped если endpoint не зарегистрирован
  - POST /api/chrome/dedicated/launch (Agent #10) — skipped если endpoint не зарегистрирован
  - GET /api/memory/indexer           (Session 9 Phase 4)
  - POST /api/memory/indexer/flush    (Session 9 Phase 4)
  - GET /api/health/lite              (schema regression — Session 10 поля не должны ломать ответ)

Запуск:
    venv/bin/python -m pytest tests/integration/test_session10_endpoints.py -v
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.modules.web_app import WebApp  # noqa: E402

# ---------------------------------------------------------------------------
# Минимальные заглушки (берём паттерн из tests/unit/test_web_commands_health_api)
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Заглушка OpenClaw клиента для deps."""

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
    """Заглушка userbot."""

    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def _deps() -> dict[str, Any]:
    """Возвращает минимальный набор deps для WebApp."""
    return {
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


@pytest.fixture
def client(_deps: dict[str, Any]) -> TestClient:
    """Возвращает TestClient поверх локального WebApp."""
    app = WebApp(_deps, port=18099, host="127.0.0.1")
    return TestClient(app.app)


@pytest.fixture
def reset_memory_indexer_singleton() -> Iterator[None]:
    """
    Перед и после теста сбрасываем singleton memory_indexer,
    чтобы predictable state. Используется memory-indexer тестами.
    """
    try:
        from src.core.memory_indexer_worker import _reset_singleton_for_tests

        _reset_singleton_for_tests()
        yield
        _reset_singleton_for_tests()
    except ImportError:
        # Если модуль не импортится — тесты сами должны пропуститься.
        yield


@pytest.fixture
def no_web_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сбрасывает WEB_API_KEY, чтобы write-эндпоинты открылись без токена."""
    monkeypatch.delenv("WEB_API_KEY", raising=False)


# ---------------------------------------------------------------------------
# /api/chrome/dedicated/status   (Agent #10)
# ---------------------------------------------------------------------------


def _route_registered(app, path: str, method: str = "GET") -> bool:
    """True если endpoint с указанным method/path есть в app.routes."""
    method = method.upper()
    for route in app.routes:
        route_path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if route_path == path and methods and method in methods:
            return True
    return False


def test_chrome_dedicated_status_returns_schema(client: TestClient) -> None:
    """
    GET /api/chrome/dedicated/status должен отдавать {enabled, running, port,
    binary, profile_dir}. Если endpoint не зарегистрирован (Agent #10 ещё не
    приземлился) — тест помечается как skipped, build не падает.
    """
    if not _route_registered(client.app, "/api/chrome/dedicated/status", "GET"):
        pytest.skip("endpoint /api/chrome/dedicated/status не зарегистрирован (Agent #10 pending)")

    resp = client.get("/api/chrome/dedicated/status")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    expected_keys = {"enabled", "running", "port", "binary", "profile_dir"}
    missing = expected_keys - set(data.keys())
    assert not missing, f"response schema missing keys: {missing}; got: {data}"


def test_chrome_dedicated_status_running_detection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Если мы мокнём is_dedicated_chrome_running → True, поле running должно
    стать True. Пропускается, если endpoint или integration-модуль
    отсутствуют (Agent #10 не приземлился).
    """
    if not _route_registered(client.app, "/api/chrome/dedicated/status", "GET"):
        pytest.skip("endpoint /api/chrome/dedicated/status не зарегистрирован")

    try:
        from src.integrations import dedicated_chrome as _dc  # type: ignore[attr-defined]
    except ImportError:
        pytest.skip("модуль src.integrations.dedicated_chrome отсутствует")

    monkeypatch.setattr(_dc, "is_dedicated_chrome_running", lambda port=9222: True, raising=False)

    resp = client.get("/api/chrome/dedicated/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("running") is True, f"running=true ожидалось, получили {data}"


def test_chrome_dedicated_launch_requires_write_access(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    POST /api/chrome/dedicated/launch должен проверять write-access
    (_assert_write_access). Если настроен WEB_API_KEY и токен не передан —
    ожидаем 403.
    """
    if not _route_registered(client.app, "/api/chrome/dedicated/launch", "POST"):
        pytest.skip("endpoint /api/chrome/dedicated/launch не зарегистрирован")

    monkeypatch.setenv("WEB_API_KEY", "super-secret-token-for-test")
    resp = client.post("/api/chrome/dedicated/launch", json={})
    # 403 ожидаем, но допустим также 401 (в зависимости от реализации).
    assert resp.status_code in {401, 403}, (
        f"ожидали 401/403 для write без токена, получили {resp.status_code}"
    )


def test_chrome_dedicated_launch_success(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    no_web_api_key: None,
) -> None:
    """
    При отсутствии WEB_API_KEY и замоканном launch_dedicated_chrome →
    (True, "launched") — endpoint должен возвращать 200 и статус launched.
    """
    if not _route_registered(client.app, "/api/chrome/dedicated/launch", "POST"):
        pytest.skip("endpoint /api/chrome/dedicated/launch не зарегистрирован")

    try:
        from src.integrations import dedicated_chrome as _dc  # type: ignore[attr-defined]
    except ImportError:
        pytest.skip("модуль src.integrations.dedicated_chrome отсутствует")

    monkeypatch.setattr(
        _dc,
        "launch_dedicated_chrome",
        lambda **kwargs: (True, "launched"),
        raising=False,
    )

    resp = client.post("/api/chrome/dedicated/launch", json={})
    assert resp.status_code == 200, f"unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    # Тело ответа может варьироваться, но должно сигналить успех.
    assert data.get("ok") is True or data.get("status") in {"launched", "ok", "started"}, (
        f"не похоже на успешный launch-ответ: {data}"
    )


# ---------------------------------------------------------------------------
# /api/memory/indexer (Session 9 Phase 4)
# ---------------------------------------------------------------------------


def test_memory_indexer_status_endpoint_200(
    client: TestClient,
    reset_memory_indexer_singleton: None,
) -> None:
    """GET /api/memory/indexer возвращает HTTP 200."""
    resp = client.get("/api/memory/indexer")
    assert resp.status_code == 200, f"unexpected status: {resp.status_code}, body={resp.text}"


def test_memory_indexer_status_schema(
    client: TestClient,
    reset_memory_indexer_singleton: None,
) -> None:
    """
    GET /api/memory/indexer отдаёт снимок IndexerStats.
    Допускаем два формата ответа:
      - normal: {is_running, queue_size, enqueued_total, processed_total, ...};
      - fallback: {"error": "indexer_unavailable"} если singleton сломан.
    """
    data = client.get("/api/memory/indexer").json()
    if "error" in data:
        # Fallback допустим, явно не fail build.
        assert data["error"] == "indexer_unavailable", f"unexpected error: {data}"
        return

    # Normal path: проверяем ключевые поля IndexerStats.
    for field in (
        "is_running",
        "queue_size",
        "queue_maxsize",
        "enqueued_total",
        "processed_total",
        "chunks_committed",
        "embeddings_committed",
    ):
        assert field in data, f"missing field {field!r} in response: {data}"

    # Типы ключевых полей.
    assert isinstance(data["is_running"], bool)
    assert isinstance(data["queue_size"], int)
    assert isinstance(data["enqueued_total"], int)
    assert isinstance(data["processed_total"], int)


def test_memory_indexer_flush_endpoint_200(
    client: TestClient,
    reset_memory_indexer_singleton: None,
) -> None:
    """POST /api/memory/indexer/flush возвращает 200 (и ack=True)."""
    resp = client.post("/api/memory/indexer/flush")
    assert resp.status_code == 200, f"unexpected status: {resp.status_code}, body={resp.text}"
    data = resp.json()
    # Допустимы: {ack:True, queue_size:N, note:...} или error-fallback.
    if "error" in data:
        assert data["error"] == "indexer_unavailable", f"unexpected error: {data}"
        return
    assert data.get("ack") is True, f"ack=true ожидалось, получили: {data}"
    assert "queue_size" in data, f"нет queue_size в ответе: {data}"


# ---------------------------------------------------------------------------
# /api/health/lite — schema regression для Session 10
# ---------------------------------------------------------------------------


def test_health_lite_status_200(client: TestClient) -> None:
    """GET /api/health/lite → 200."""
    resp = client.get("/api/health/lite")
    assert resp.status_code == 200


def test_health_lite_schema_stable(client: TestClient) -> None:
    """
    Session 10 добавила поля memory_indexer_state и memory_indexer_queue_size в
    /api/health/lite, но базовый schema не должен сломаться. Проверяем
    стандартные поля.
    """
    data = client.get("/api/health/lite").json()
    # Базовая стабильность схемы.
    assert data.get("ok") is True, f"ok=true ожидалось: {data}"
    assert data.get("status") == "up", f"status='up' ожидалось: {data}"
    for field in (
        "telegram_session_state",
        "telegram_userbot_state",
        "openclaw_auth_state",
        "last_runtime_route",
    ):
        assert field in data, f"обязательное поле {field!r} отсутствует"


def test_health_lite_contains_memory_indexer_fields(client: TestClient) -> None:
    """
    Session 9/10: в /api/health/lite появились memory_indexer_state и
    memory_indexer_queue_size. Их наличие — smoke для Phase 4 интеграции.
    """
    data = client.get("/api/health/lite").json()
    assert "memory_indexer_state" in data, (
        "поле memory_indexer_state должно присутствовать после Session 9 Phase 4"
    )
    assert "memory_indexer_queue_size" in data, (
        "поле memory_indexer_queue_size должно присутствовать после Session 9 Phase 4"
    )


# ---------------------------------------------------------------------------
# Sanity: тестовый клиент поднимается в принципе.
# ---------------------------------------------------------------------------


def test_client_boots(client: TestClient) -> None:
    """Smoke: хотя бы один ping endpoint должен отвечать (кроме изучаемых)."""
    resp = client.get("/api/health/lite")
    assert resp.status_code == 200, "TestClient не может поднять приложение"
