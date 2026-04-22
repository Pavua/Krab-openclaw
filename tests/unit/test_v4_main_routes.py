# -*- coding: utf-8 -*-
"""
Тесты маршрутов после промоутинга V4 dashboards на главные адреса (session 14).

Схема:
  /costs, /inbox, /swarm, /ops, /settings, /translator, /commands
    → V4 контент (liquid-glass.css присутствует в HTML)
  /v4/costs, /v4/inbox, ...
    → 301 permanent redirect → /costs, /inbox, ...
  /legacy/costs, /legacy/inbox, /legacy/swarm, /legacy/translator
    → v3 контент (без liquid-glass.css)
  /legacy/ops, /legacy/settings, /legacy/commands
    → placeholder HTML (200)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ---------------------------------------------------------------------------
# Заглушки
# ---------------------------------------------------------------------------


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


def _make_client() -> TestClient:
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
    app = WebApp(deps, port=18092, host="127.0.0.1")
    # Не следовать редиректам — нам нужно проверить 301 напрямую
    return TestClient(app.app, follow_redirects=False)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return _make_client()


# ---------------------------------------------------------------------------
# Вспомогательные константы
# ---------------------------------------------------------------------------

_V4_PAGES = ["costs", "inbox", "swarm", "ops", "settings", "translator", "commands"]
_V3_PAGES_WITH_HTML = ["costs", "inbox", "swarm", "translator"]
_LEGACY_PLACEHOLDER_PAGES = ["ops", "settings", "commands"]

# Маркер, уникальный для V4 (liquid-glass CSS ссылка присутствует только в V4)
_V4_MARKER = "liquid-glass.css"


# ---------------------------------------------------------------------------
# Primary routes → V4 контент
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", _V4_PAGES)
def test_primary_route_returns_200(client: TestClient, page: str) -> None:
    """GET /<page> возвращает 200 OK."""
    resp = client.get(f"/{page}")
    assert resp.status_code == 200, f"/{page} должен вернуть 200, получили {resp.status_code}"


@pytest.mark.parametrize("page", _V4_PAGES)
def test_primary_route_is_html(client: TestClient, page: str) -> None:
    """GET /<page> отдаёт text/html."""
    resp = client.get(f"/{page}")
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.parametrize("page", _V4_PAGES)
def test_primary_route_contains_v4_marker(client: TestClient, page: str) -> None:
    """GET /<page> содержит маркер V4 (liquid-glass.css) — значит это V4 дашборд."""
    resp = client.get(f"/{page}")
    assert _V4_MARKER in resp.text, (
        f"/{page} должен содержать '{_V4_MARKER}' (V4 контент), но его нет"
    )


@pytest.mark.parametrize("page", _V4_PAGES)
def test_primary_route_no_store_cache(client: TestClient, page: str) -> None:
    """Primary routes отдают Cache-Control: no-store."""
    resp = client.get(f"/{page}")
    assert "no-store" in resp.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# /v4/* → 301 redirect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", _V4_PAGES)
def test_v4_prefix_redirects_301(client: TestClient, page: str) -> None:
    """GET /v4/<page> отдаёт 301 Permanent Redirect."""
    resp = client.get(f"/v4/{page}")
    assert resp.status_code == 301, f"/v4/{page} должен вернуть 301, получили {resp.status_code}"


@pytest.mark.parametrize("page", _V4_PAGES)
def test_v4_prefix_redirect_target(client: TestClient, page: str) -> None:
    """GET /v4/<page> редиректит на /<page>."""
    resp = client.get(f"/v4/{page}")
    location = resp.headers.get("location", "")
    assert location.endswith(f"/{page}"), (
        f"/v4/{page} должен редиректить на /{page}, location={location!r}"
    )


# ---------------------------------------------------------------------------
# /legacy/* → v3 контент (200)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("page", _V3_PAGES_WITH_HTML + _LEGACY_PLACEHOLDER_PAGES)
def test_legacy_route_returns_200(client: TestClient, page: str) -> None:
    """GET /legacy/<page> возвращает 200 OK."""
    resp = client.get(f"/legacy/{page}")
    assert resp.status_code == 200, (
        f"/legacy/{page} должен вернуть 200, получили {resp.status_code}"
    )


@pytest.mark.parametrize("page", _V3_PAGES_WITH_HTML + _LEGACY_PLACEHOLDER_PAGES)
def test_legacy_route_is_html(client: TestClient, page: str) -> None:
    """GET /legacy/<page> отдаёт text/html."""
    resp = client.get(f"/legacy/{page}")
    assert "text/html" in resp.headers.get("content-type", "")


@pytest.mark.parametrize("page", _V3_PAGES_WITH_HTML)
def test_legacy_route_does_not_contain_v4_marker(client: TestClient, page: str) -> None:
    """GET /legacy/<page> НЕ содержит liquid-glass.css — это v3 дашборд."""
    resp = client.get(f"/legacy/{page}")
    # v3 HTML файлы не должны иметь v4 маркер (если файл существует)
    if "Legacy" not in resp.text and "<h1>" not in resp.text[:50]:
        # Файл найден и отдан — проверяем что нет v4 маркера
        assert _V4_MARKER not in resp.text, (
            f"/legacy/{page} не должен содержать '{_V4_MARKER}' (это должна быть v3 версия)"
        )


@pytest.mark.parametrize("page", _LEGACY_PLACEHOLDER_PAGES)
def test_legacy_placeholder_contains_link_to_v4(client: TestClient, page: str) -> None:
    """Legacy placeholder для ops/settings/commands содержит ссылку на V4 версию."""
    resp = client.get(f"/legacy/{page}")
    assert f"/{page}" in resp.text, f"/legacy/{page} placeholder должен содержать ссылку на /{page}"
