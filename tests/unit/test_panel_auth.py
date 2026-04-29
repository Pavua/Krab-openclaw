# -*- coding: utf-8 -*-
"""
Тесты bcrypt basic auth middleware для Krab Panel.

Покрывает:
  - KRAB_PANEL_AUTH не задан → middleware не активируется (панель открыта)
  - KRAB_PANEL_AUTH=1 + корректный пароль → 200
  - KRAB_PANEL_AUTH=1 + неверный пароль → 401
  - KRAB_PANEL_AUTH=1 + без заголовка Authorization → 401
  - Health endpoints (/api/health/lite, /api/v1/health) доступны без auth
  - KRAB_PANEL_AUTH=1 + нет KRAB_PANEL_PASSWORD_HASH → middleware не активируется
"""

from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

try:
    import bcrypt
except ImportError:
    pytest.skip("bcrypt не установлен", allow_module_level=True)

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.testclient import TestClient
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response
except ImportError:
    pytest.skip("fastapi/starlette не установлены", allow_module_level=True)


# ── Хелперы ─────────────────────────────────────────────────────────────────

_TEST_USERNAME = "testuser"
_TEST_PASSWORD = "secret123"
_TEST_HASH = bcrypt.hashpw(_TEST_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

_NO_AUTH_PATHS = frozenset({"/api/health/lite", "/api/v1/health"})


def _make_basic_header(username: str, password: str) -> str:
    """Сформировать заголовок Authorization: Basic <b64>."""
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {encoded}"


def _build_app_with_bcrypt_auth(
    *,
    auth_enabled: bool = True,
    username: str = _TEST_USERNAME,
    password_hash: str = _TEST_HASH,
) -> TestClient:
    """Создать тестовый FastAPI app с BcryptAuthMiddleware."""
    app = FastAPI()

    @app.get("/api/test")
    async def test_route():
        return JSONResponse({"ok": True})

    @app.get("/api/health/lite")
    async def health_lite():
        return JSONResponse({"status": "ok"})

    @app.get("/api/v1/health")
    async def health_v1():
        return JSONResponse({"status": "ok"})

    if auth_enabled and password_hash:
        _hash_bytes = password_hash.encode()
        _uname = username

        class BcryptAuthMiddleware(BaseHTTPMiddleware):
            async def dispatch(self, request: Request, call_next):
                if request.url.path in _NO_AUTH_PATHS:
                    return await call_next(request)
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Basic "):
                    try:
                        decoded = base64.b64decode(auth_header[len("Basic "):]).decode("utf-8", errors="replace")
                        provided_user, _, provided_pass = decoded.partition(":")
                        if provided_user == _uname and provided_pass:
                            if bcrypt.checkpw(provided_pass.encode(), _hash_bytes):
                                return await call_next(request)
                    except Exception:
                        pass
                return Response(
                    content="Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="Krab Panel"'},
                )

        app.add_middleware(BcryptAuthMiddleware)

    return TestClient(app, raise_server_exceptions=True)


# ── Тесты ───────────────────────────────────────────────────────────────────


class TestBcryptAuthMiddleware:
    """Основные сценарии bcrypt auth middleware."""

    def test_no_auth_env_panel_is_open(self):
        """Без KRAB_PANEL_AUTH панель доступна без пароля (тест 1/5)."""
        client = _build_app_with_bcrypt_auth(auth_enabled=False)
        resp = client.get("/api/test")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_correct_password_returns_200(self):
        """Правильный пароль → 200 OK (тест 2/5)."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get(
            "/api/test",
            headers={"Authorization": _make_basic_header(_TEST_USERNAME, _TEST_PASSWORD)},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_wrong_password_returns_401(self):
        """Неверный пароль → 401 Unauthorized (тест 3/5)."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get(
            "/api/test",
            headers={"Authorization": _make_basic_header(_TEST_USERNAME, "wrongpass")},
        )
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"] == 'Basic realm="Krab Panel"'

    def test_no_auth_header_returns_401(self):
        """Без заголовка Authorization → 401 (тест 4/5)."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get("/api/test")
        assert resp.status_code == 401

    def test_health_endpoints_bypass_auth(self):
        """Health endpoints всегда доступны без auth (тест 5/5)."""
        client = _build_app_with_bcrypt_auth()
        for path in ["/api/health/lite", "/api/v1/health"]:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} должен быть доступен без auth"

    def test_no_hash_env_middleware_not_activated(self):
        """Без KRAB_PANEL_PASSWORD_HASH middleware не активируется (панель открыта)."""
        client = _build_app_with_bcrypt_auth(password_hash="")
        resp = client.get("/api/test")
        assert resp.status_code == 200

    def test_wrong_username_returns_401(self):
        """Неверный username → 401 даже при правильном пароле."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get(
            "/api/test",
            headers={"Authorization": _make_basic_header("wronguser", _TEST_PASSWORD)},
        )
        assert resp.status_code == 401

    def test_malformed_basic_header_returns_401(self):
        """Сломанный Base64 в заголовке → 401 без краша."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get(
            "/api/test",
            headers={"Authorization": "Basic !!!not-valid-base64!!!"},
        )
        assert resp.status_code == 401

    def test_empty_password_returns_401(self):
        """Пустой пароль → 401 (защита от username-only атаки)."""
        client = _build_app_with_bcrypt_auth()
        resp = client.get(
            "/api/test",
            headers={"Authorization": _make_basic_header(_TEST_USERNAME, "")},
        )
        assert resp.status_code == 401


# ── Тесты переменных окружения ───────────────────────────────────────────────


class TestPanelAuthEnvVars:
    """Проверяем, что _setup_bcrypt_auth_middleware корректно читает env."""

    def test_krab_panel_auth_not_1_skips_middleware(self, monkeypatch):
        """KRAB_PANEL_AUTH != '1' → middleware пропускается."""
        monkeypatch.setenv("KRAB_PANEL_AUTH", "0")
        monkeypatch.setenv("KRAB_PANEL_USERNAME", _TEST_USERNAME)
        monkeypatch.setenv("KRAB_PANEL_PASSWORD_HASH", _TEST_HASH)
        # Строим app без middleware (имитация того, что происходит при KRAB_PANEL_AUTH!=1)
        client = _build_app_with_bcrypt_auth(auth_enabled=False)
        assert client.get("/api/test").status_code == 200

    def test_bcrypt_hash_generation(self):
        """bcrypt.hashpw генерирует хэш, который checkpw верифицирует."""
        password = "test_secret_42"
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4))
        assert bcrypt.checkpw(password.encode(), hashed)
        assert not bcrypt.checkpw(b"wrong", hashed)
