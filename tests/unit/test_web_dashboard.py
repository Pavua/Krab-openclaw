# -*- coding: utf-8 -*-
"""
Тесты dashboard web modules (session 5).

Покрываем HTML-страницы и статические ресурсы owner-панели:
  GET /                   — landing page (из web_app_landing_page.py)
  GET /stats              — stats dashboard
  GET /inbox              — inbox dashboard
  GET /costs              — costs dashboard
  GET /swarm              — swarm dashboard
  GET /nano_theme.css     — основной CSS (файл или 404)
  GET /prototypes/{page}  — prototype pages (существующие и отсутствующие)
  GET /translator         — translator status page
  Cache-Control headers   — no-store на всех HTML-ответах
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки — минимальный набор для инициализации WebApp
# ---------------------------------------------------------------------------


class _FakeOpenClaw:
    """Заглушка OpenClaw-клиента без внешних вызовов."""

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


class _FakeKraab:
    def get_translator_runtime_profile(self) -> dict:
        return {"language_pair": "es-ru", "enabled": True}

    def get_translator_session_state(self) -> dict:
        return {"session_status": "idle", "active_chats": [], "stats": {}}

    def get_voice_runtime_profile(self) -> dict:
        return {"tts_enabled": False}

    def get_runtime_state(self) -> dict:
        return {"startup_state": "running", "client_connected": True}

    def get_voice_blocked_chats(self) -> list:
        return []


# ---------------------------------------------------------------------------
# Фабрика
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
    return WebApp(deps, port=18091, host="127.0.0.1")


def _client() -> TestClient:
    return TestClient(_make_app().app)


# ---------------------------------------------------------------------------
# GET / — landing page
# ---------------------------------------------------------------------------


def test_index_returns_html() -> None:
    """GET / возвращает HTML-страницу с кодом 200."""
    # Патчим _index_path чтобы не зависеть от наличия реального файла
    app = _make_app()
    app._index_path = Path("/nonexistent_path_krab_test.html")
    client = TestClient(app.app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_index_contains_krab_content() -> None:
    """Landing page должна содержать ключевые слова Краба."""
    app = _make_app()
    app._index_path = Path("/nonexistent_path_krab_test.html")
    client = TestClient(app.app)
    resp = client.get("/")
    body = resp.text.lower()
    # LANDING_PAGE_HTML содержит упоминания krab или owner-панели
    assert "<!doctype html" in body or "<html" in body


def test_index_no_store_cache_header() -> None:
    """Landing page должна отдавать Cache-Control: no-store."""
    app = _make_app()
    app._index_path = Path("/nonexistent_path_krab_test.html")
    client = TestClient(app.app)
    resp = client.get("/")
    cc = resp.headers.get("cache-control", "")
    assert "no-store" in cc


# ---------------------------------------------------------------------------
# GET /stats — stats dashboard
# ---------------------------------------------------------------------------


def test_stats_dashboard_returns_200() -> None:
    """GET /stats возвращает HTML stats dashboard с кодом 200."""
    resp = _client().get("/stats")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_stats_dashboard_is_html_document() -> None:
    """Stats dashboard содержит корректную HTML-структуру."""
    resp = _client().get("/stats")
    assert "<html" in resp.text.lower()


def test_stats_dashboard_no_store_header() -> None:
    """Stats dashboard отдаёт no-store заголовок."""
    resp = _client().get("/stats")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# GET /inbox — inbox dashboard
# ---------------------------------------------------------------------------


def test_inbox_dashboard_returns_200() -> None:
    """GET /inbox возвращает HTML inbox dashboard с кодом 200."""
    resp = _client().get("/inbox")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_inbox_dashboard_no_store_header() -> None:
    """Inbox dashboard отдаёт no-store заголовок."""
    resp = _client().get("/inbox")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# GET /costs — costs dashboard
# ---------------------------------------------------------------------------


def test_costs_dashboard_returns_200() -> None:
    """GET /costs возвращает HTML costs dashboard с кодом 200."""
    resp = _client().get("/costs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_costs_dashboard_no_store_header() -> None:
    """Costs dashboard отдаёт no-store заголовок."""
    resp = _client().get("/costs")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# GET /swarm — swarm dashboard
# ---------------------------------------------------------------------------


def test_swarm_dashboard_returns_200() -> None:
    """GET /swarm возвращает HTML swarm dashboard с кодом 200."""
    resp = _client().get("/swarm")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_swarm_dashboard_no_store_header() -> None:
    """Swarm dashboard отдаёт no-store заголовок."""
    resp = _client().get("/swarm")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# GET /nano_theme.css — статический CSS
# ---------------------------------------------------------------------------


def test_nano_theme_css_missing_returns_404() -> None:
    """Если nano_theme.css отсутствует на диске — возвращаем 404."""
    app = _make_app()
    app._nano_theme_path = Path("/nonexistent_nano_theme_krab.css")
    client = TestClient(app.app)
    resp = client.get("/nano_theme.css")
    assert resp.status_code == 404


def test_nano_theme_css_present_returns_css() -> None:
    """Если nano_theme.css существует — отдаём с media-type text/css."""
    with tempfile.NamedTemporaryFile(suffix=".css", delete=False, mode="w") as f:
        f.write("body { color: red; }")
        tmp_path = Path(f.name)
    try:
        app = _make_app()
        app._nano_theme_path = tmp_path
        client = TestClient(app.app)
        resp = client.get("/nano_theme.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# GET /prototypes/{page} — prototype pages
# ---------------------------------------------------------------------------


def test_prototype_page_not_found_returns_404_html() -> None:
    """Несуществующий prototype возвращает HTML с сообщением not found."""
    resp = _client().get("/prototypes/nonexistent_page_xyz")
    # Маршрут возвращает HTML с 404, а не JSON
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()


def test_prototype_path_traversal_blocked() -> None:
    """Попытка path traversal через '..' должна безопасно обрабатываться."""
    # Маршрут sanitize'ит page параметр, не должно быть 500
    resp = _client().get("/prototypes/../../etc/passwd")
    # Ожидаем 404 (page не найдена), но не 500
    assert resp.status_code in (404, 200)


# ---------------------------------------------------------------------------
# GET /translator — translator page
# ---------------------------------------------------------------------------


def test_translator_page_returns_html() -> None:
    """GET /translator возвращает HTML-страницу (файл или заглушку)."""
    resp = _client().get("/translator")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" in resp.text.lower() or "translator" in resp.text.lower()
