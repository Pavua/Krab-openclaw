# -*- coding: utf-8 -*-
"""
Тесты Browser Bridge API эндпоинтов owner-панели Krab.

Покрываем маршруты /api/browser/*:
  GET  /api/browser/status      — статус подключения к браузеру
  GET  /api/browser/tabs        — список открытых вкладок
  POST /api/browser/navigate    — навигация на URL
  POST /api/browser/screenshot  — снимок экрана браузера (base64)
  POST /api/browser/read        — чтение текста страницы
  POST /api/browser/js          — выполнение JS в браузере

Примечание: /api/macos/* эндпоинтов в web_app.py нет —
macOS automation используется только как probe внутри /api/health.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import src.integrations.browser_bridge as _bb_module
from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки зависимостей WebApp
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    def get_last_runtime_route(self) -> dict:
        return {
            "channel": "cloud",
            "provider": "google",
            "model": "gemini-test",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self) -> dict:
        return {"active_tier": "free", "last_error_code": None}

    async def health_check(self) -> bool:
        return True


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": False}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}


class _DummyRouter:
    def get_model_info(self) -> dict:
        return {}


class _FakeHealthClient:
    async def health_check(self) -> bool:
        return True

    async def health_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake"}

    async def capabilities_report(self) -> dict:
        return {"ok": True, "status": "ok", "source": "fake", "detail": {}}


# ---------------------------------------------------------------------------
# Мок browser_bridge и фабрика клиента
# ---------------------------------------------------------------------------


def _make_mock_browser(
    *,
    attached: bool = True,
    tabs: list[dict] | None = None,
    navigate_url: str = "https://example.com",
    screenshot_data: str | None = "base64data==",
    page_text: str = "Hello page",
    js_result: object = 42,
    raise_exc: bool = False,
) -> AsyncMock:
    """Возвращает AsyncMock с нужными методами browser_bridge."""
    tabs = tabs if tabs is not None else [{"url": "https://example.com", "title": "Example"}]

    m = AsyncMock()
    if raise_exc:
        exc = TimeoutError("симуляция таймаута")
        m.is_attached = AsyncMock(side_effect=exc)
        m.list_tabs = AsyncMock(side_effect=exc)
        m.navigate = AsyncMock(side_effect=exc)
        m.screenshot_base64 = AsyncMock(side_effect=exc)
        m.get_page_text = AsyncMock(side_effect=exc)
        m.execute_js = AsyncMock(side_effect=exc)
    else:
        m.is_attached = AsyncMock(return_value=attached)
        m.list_tabs = AsyncMock(return_value=tabs)
        m.navigate = AsyncMock(return_value=navigate_url)
        m.screenshot_base64 = AsyncMock(return_value=screenshot_data)
        m.get_page_text = AsyncMock(return_value=page_text)
        m.execute_js = AsyncMock(return_value=js_result)
    return m


@contextmanager
def _client_with_browser(**kwargs):
    """
    Контекстный менеджер: патчит browser_bridge в модуле ДО создания WebApp,
    чтобы замыкание роутов захватило мок.
    Возвращает TestClient.
    """
    mock_bb = _make_mock_browser(**kwargs)
    # Патчим атрибут модуля — именно оттуда импортируется browser_bridge в web_app
    original = _bb_module.browser_bridge
    _bb_module.browser_bridge = mock_bb
    try:
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
        yield TestClient(app.app)
    finally:
        _bb_module.browser_bridge = original


# ---------------------------------------------------------------------------
# GET /api/browser/status
# ---------------------------------------------------------------------------


def test_browser_status_attached() -> None:
    """GET /api/browser/status: attached=True, возвращает корректный tab_count."""
    tabs = [{"url": "https://a.com"}, {"url": "https://b.com"}]
    with _client_with_browser(attached=True, tabs=tabs) as client:
        resp = client.get("/api/browser/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["attached"] is True
    assert data["tab_count"] == 2
    assert data["active_url"] == "https://b.com"


def test_browser_status_not_attached() -> None:
    """GET /api/browser/status: attached=False, tabs пустой."""
    with _client_with_browser(attached=False, tabs=[]) as client:
        resp = client.get("/api/browser/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["attached"] is False
    assert data["tab_count"] == 0
    assert data["active_url"] is None


def test_browser_status_timeout() -> None:
    """GET /api/browser/status: исключение в is_attached → ok=False, attached=False."""
    with _client_with_browser(raise_exc=True) as client:
        resp = client.get("/api/browser/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["attached"] is False
    assert data["tab_count"] == 0


# ---------------------------------------------------------------------------
# GET /api/browser/tabs
# ---------------------------------------------------------------------------


def test_browser_tabs_returns_list() -> None:
    """GET /api/browser/tabs: возвращает список вкладок от мока."""
    tabs = [{"url": "https://example.com", "title": "Ex"}]
    with _client_with_browser(tabs=tabs) as client:
        resp = client.get("/api/browser/tabs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["url"] == "https://example.com"


def test_browser_tabs_timeout_returns_error() -> None:
    """GET /api/browser/tabs: таймаут → ok=False, tabs=[]."""
    with _client_with_browser(raise_exc=True) as client:
        resp = client.get("/api/browser/tabs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["tabs"] == []


# ---------------------------------------------------------------------------
# POST /api/browser/navigate
# ---------------------------------------------------------------------------


def test_browser_navigate_ok() -> None:
    """POST /api/browser/navigate: корректный URL → ok=True, current_url из мока."""
    with _client_with_browser(navigate_url="https://example.com") as client:
        resp = client.post("/api/browser/navigate", json={"url": "https://example.com"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["current_url"] == "https://example.com"


def test_browser_navigate_missing_url() -> None:
    """POST /api/browser/navigate без url → 400."""
    with _client_with_browser() as client:
        resp = client.post("/api/browser/navigate", json={})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/browser/screenshot
# ---------------------------------------------------------------------------


def test_browser_screenshot_ok() -> None:
    """POST /api/browser/screenshot: возвращает base64 данные от мока."""
    with _client_with_browser(screenshot_data="abc123==") as client:
        resp = client.post("/api/browser/screenshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["data"] == "abc123=="


def test_browser_screenshot_none_data() -> None:
    """POST /api/browser/screenshot: screenshot_base64 вернул None → ok=False."""
    with _client_with_browser(screenshot_data=None) as client:
        resp = client.post("/api/browser/screenshot")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "screenshot_failed"


# ---------------------------------------------------------------------------
# POST /api/browser/read
# ---------------------------------------------------------------------------


def test_browser_read_ok() -> None:
    """POST /api/browser/read: возвращает текст страницы от мока."""
    with _client_with_browser(page_text="Содержимое страницы") as client:
        resp = client.post("/api/browser/read")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["text"] == "Содержимое страницы"


# ---------------------------------------------------------------------------
# POST /api/browser/js
# ---------------------------------------------------------------------------


def test_browser_js_ok() -> None:
    """POST /api/browser/js: выполняет код и возвращает результат от мока."""
    with _client_with_browser(js_result={"answer": 42}) as client:
        resp = client.post("/api/browser/js", json={"code": "return 42;"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["result"] == {"answer": 42}


def test_browser_js_missing_code() -> None:
    """POST /api/browser/js без code → 400."""
    with _client_with_browser() as client:
        resp = client.post("/api/browser/js", json={})
    assert resp.status_code == 400
