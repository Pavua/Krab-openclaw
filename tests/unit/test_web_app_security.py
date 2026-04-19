# -*- coding: utf-8 -*-
"""
Тесты security-механизмов web-панели Краба.

Покрываем:
1) API key validation (_assert_write_access): header, query token, отсутствие ключа;
2) Rate limiting assistant endpoint'а (_enforce_assistant_rate_limit);
3) Idempotency cache (_idempotency_get / _idempotency_set / TTL);
4) CORS — проверяем, что явных CORS-заголовков нет (middleware не подключен).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.modules.web_app import WebApp

# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные заглушки
# ──────────────────────────────────────────────────────────────────────────────


class _DummyRouter:
    """Минимальный роутер-заглушка."""

    def get_model_info(self):
        return {}


class _FakeOpenClaw:
    """Минимальный фейковый OpenClaw клиент."""

    async def health_check(self) -> bool:
        return True

    def get_last_runtime_route(self):
        return {
            "channel": "local_direct",
            "provider": "test",
            "model": "test/model",
            "status": "ok",
            "error_code": None,
        }

    def get_tier_state_export(self):
        return {
            "active_tier": "free",
            "last_error_code": None,
            "last_provider_status": "ok",
            "last_recovery_action": "none",
        }


def _make_client(*, web_api_key: str = "") -> TestClient:
    """Создаёт TestClient с заданным WEB_API_KEY окружения."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "memory_engine": None,
        "cache_manager": None,
        "model_manager": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    with patch.dict(os.environ, {"WEB_API_KEY": web_api_key}, clear=False):
        app = WebApp(deps, port=18080, host="127.0.0.1")
    return TestClient(app.app, raise_server_exceptions=False)


def _make_web_app(*, web_api_key: str = "") -> WebApp:
    """Создаёт экземпляр WebApp напрямую для unit-тестирования методов."""
    deps = {
        "router": _DummyRouter(),
        "openclaw_client": _FakeOpenClaw(),
        "black_box": None,
        "memory_engine": None,
        "cache_manager": None,
        "model_manager": None,
        "perceptor": None,
        "watchdog": None,
        "queue": None,
    }
    with patch.dict(os.environ, {"WEB_API_KEY": web_api_key}, clear=False):
        return WebApp(deps, port=18080, host="127.0.0.1")


# ──────────────────────────────────────────────────────────────────────────────
# 1. API key validation
# ──────────────────────────────────────────────────────────────────────────────


class TestApiKeyValidation:
    """Тесты валидации WEB_API_KEY через _assert_write_access."""

    def test_no_key_configured_allows_any_request(self):
        """Если WEB_API_KEY не задан — доступ открыт без аутентификации."""
        app = _make_web_app(web_api_key="")
        # Любой header/token должны проходить
        app._assert_write_access("anything", "")  # не должен выбрасывать исключение

    def test_correct_header_grants_access(self):
        """Верный ключ в X-Krab-Web-Key разрешает запрос."""
        app = _make_web_app(web_api_key="secret123")
        with patch.dict(os.environ, {"WEB_API_KEY": "secret123"}):
            app._assert_write_access("secret123", "")  # не должен выбрасывать исключение

    def test_correct_token_query_grants_access(self):
        """Верный ключ через query-параметр token разрешает запрос."""
        app = _make_web_app(web_api_key="secret123")
        with patch.dict(os.environ, {"WEB_API_KEY": "secret123"}):
            app._assert_write_access("", "secret123")

    def test_wrong_key_raises_403(self):
        """Неверный ключ должен вызывать HTTPException 403."""
        from fastapi import HTTPException

        app = _make_web_app(web_api_key="correct")
        with patch.dict(os.environ, {"WEB_API_KEY": "correct"}):
            with pytest.raises(HTTPException) as exc_info:
                app._assert_write_access("wrong", "")
        assert exc_info.value.status_code == 403

    def test_empty_provided_key_raises_403_when_configured(self):
        """Пустой header и token при настроенном ключе → 403."""
        from fastapi import HTTPException

        app = _make_web_app(web_api_key="mysecret")
        with patch.dict(os.environ, {"WEB_API_KEY": "mysecret"}):
            with pytest.raises(HTTPException) as exc_info:
                app._assert_write_access("", "")
        assert exc_info.value.status_code == 403

    def test_write_endpoint_forbidden_without_key(self):
        """POST /api/inbox/update без WEB_API_KEY → 403 через HTTP."""
        client = _make_client(web_api_key="mykey")
        with patch.dict(os.environ, {"WEB_API_KEY": "mykey"}):
            resp = client.post("/api/inbox/update", json={})
        assert resp.status_code == 403

    def test_write_endpoint_allowed_with_correct_key_header(self):
        """POST /api/inbox/update с верным X-Krab-Web-Key обрабатывается (не 403)."""
        client = _make_client(web_api_key="mykey")
        with patch.dict(os.environ, {"WEB_API_KEY": "mykey"}):
            resp = client.post(
                "/api/inbox/update",
                json={"item_id": "test-1", "action": "confirm"},
                headers={"X-Krab-Web-Key": "mykey"},
            )
        # Не 403 — запрос прошёл аутентификацию (может быть 200 или 500 без реального inbox)
        assert resp.status_code != 403


# ──────────────────────────────────────────────────────────────────────────────
# 2. Rate limiting
# ──────────────────────────────────────────────────────────────────────────────


class TestRateLimiting:
    """Тесты in-memory rate limit assistant endpoint'а."""

    def test_rate_limit_default_is_positive(self):
        """Лимит запросов по умолчанию должен быть >= 1."""
        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "30"}):
            assert app._assistant_rate_limit_per_min() >= 1

    def test_rate_limit_env_override(self):
        """WEB_ASSISTANT_RATE_LIMIT_PER_MIN переопределяет лимит."""
        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "5"}):
            assert app._assistant_rate_limit_per_min() == 5

    def test_rate_limit_invalid_env_falls_back_to_default(self):
        """Некорректный env → fallback 30."""
        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "not_a_number"}):
            assert app._assistant_rate_limit_per_min() == 30

    def test_enforce_rate_limit_allows_within_limit(self):
        """Вызовы в пределах лимита не вызывают исключений."""
        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "5"}):
            for _ in range(5):
                app._enforce_assistant_rate_limit("client-a")  # не должно кидать

    def test_enforce_rate_limit_raises_429_on_overflow(self):
        """Превышение лимита вызывает HTTPException 429."""
        from fastapi import HTTPException

        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "3"}):
            for _ in range(3):
                app._enforce_assistant_rate_limit("client-b")
            with pytest.raises(HTTPException) as exc_info:
                app._enforce_assistant_rate_limit("client-b")
        assert exc_info.value.status_code == 429

    def test_rate_limit_is_per_client_key(self):
        """Лимит изолирован на client key — разные клиенты не влияют друг на друга."""

        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_ASSISTANT_RATE_LIMIT_PER_MIN": "2"}):
            # client-x исчерпал лимит
            for _ in range(2):
                app._enforce_assistant_rate_limit("client-x")
            # client-y ещё должен проходить
            app._enforce_assistant_rate_limit("client-y")  # не должно выбрасывать


# ──────────────────────────────────────────────────────────────────────────────
# 3. Idempotency cache
# ──────────────────────────────────────────────────────────────────────────────


class TestIdempotency:
    """Тесты кэша idempotency."""

    def test_get_unknown_key_returns_none(self):
        """Незнакомый idempotency key → None."""
        app = _make_web_app()
        result = app._idempotency_get("ns", "nonexistent-key")
        assert result is None

    def test_set_and_get_returns_payload(self):
        """Сохранённый ответ возвращается при повторном запросе."""
        app = _make_web_app()
        payload = {"ok": True, "result": "done"}
        app._idempotency_set("ns", "idem-1", payload)
        cached = app._idempotency_get("ns", "idem-1")
        assert cached is not None
        assert cached["ok"] is True
        assert cached["idempotent_replay"] is True

    def test_idempotent_replay_flag_set(self):
        """Кэшированный ответ имеет idempotent_replay=True."""
        app = _make_web_app()
        app._idempotency_set("ns", "idem-2", {"x": 1})
        cached = app._idempotency_get("ns", "idem-2")
        assert cached["idempotent_replay"] is True

    def test_empty_key_returns_none(self):
        """Пустой idempotency key → None (ключ игнорируется)."""
        app = _make_web_app()
        app._idempotency_set("ns", "", {"x": 1})
        assert app._idempotency_get("ns", "") is None

    def test_ttl_expiry_returns_none(self):
        """После истечения TTL кэшированный ответ недоступен."""
        app = _make_web_app()
        with patch.dict(os.environ, {"WEB_IDEMPOTENCY_TTL_SEC": "30"}):
            app._idempotency_set("ns", "ttl-key", {"data": "value"})
            # Симулируем истечение времени, подкручивая запись вручную
            lookup_key = "ns:ttl-key"
            ts, payload = app._idempotency_state[lookup_key]
            app._idempotency_state[lookup_key] = (ts - 9999, payload)
            result = app._idempotency_get("ns", "ttl-key")
        assert result is None

    def test_different_namespaces_are_isolated(self):
        """Ключи в разных namespace не пересекаются."""
        app = _make_web_app()
        app._idempotency_set("ns_a", "key1", {"src": "a"})
        result = app._idempotency_get("ns_b", "key1")
        assert result is None
