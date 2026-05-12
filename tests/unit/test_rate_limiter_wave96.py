# -*- coding: utf-8 -*-
"""Wave 96: тесты owner-panel token-bucket rate-limiter.

Покрываем:
    * проход под лимитом
    * блок при превышении (429 + Retry-After)
    * burst-логика (capacity > 1 req)
    * exempt path не учитывается лимитом
    * TTL eviction idle-ключей
    * раздельные ключи per-IP
    * env-gate is_rate_limit_enabled
    * classify_ip метрик
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from src.core.metrics.rate_limit import classify_ip
from src.modules.web_middleware.rate_limiter import (
    EXEMPT_PATHS,
    RateLimiter,
    RateLimitMiddleware,
    is_rate_limit_enabled,
)

# ── фейковые часы ─────────────────────────────────────────────────────────────


class FakeClock:
    """Прокручиваемые monotonic-часы (без asyncio.sleep)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, sec: float) -> None:
        self.now += float(sec)


# ── RateLimiter unit ──────────────────────────────────────────────────────────


def test_under_limit_passes() -> None:
    """Запросы в пределах burst проходят (allowed=True)."""
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, burst=5, clock_fn=clock)
    for _ in range(5):
        allowed, _ = limiter.check("ip:127.0.0.1")
        assert allowed is True


def test_over_limit_blocks_with_retry_after() -> None:
    """Превышение burst → allowed=False, retry_after >= 1 sec."""
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, burst=3, clock_fn=clock)
    for _ in range(3):
        ok, _ = limiter.check("ip:1.2.3.4")
        assert ok is True
    blocked, retry = limiter.check("ip:1.2.3.4")
    assert blocked is False
    assert retry >= 1.0


def test_burst_refills_over_time() -> None:
    """После истощения bucket'а — refill по rpm восстанавливает токены."""
    clock = FakeClock()
    # rpm=60 → refill 1 токен/сек. burst=2 → опустошится за 2 запроса.
    limiter = RateLimiter(rpm=60, burst=2, clock_fn=clock)
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is True
    assert limiter.check("k")[0] is False
    # Прокручиваем 1.1 сек → ровно ≥1 токен дозалит.
    clock.advance(1.1)
    assert limiter.check("k")[0] is True


def test_per_key_isolation() -> None:
    """Разные ключи имеют независимые buckets."""
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, burst=1, clock_fn=clock)
    assert limiter.check("ip:a")[0] is True
    # Bucket A исчерпан, но B свежий.
    assert limiter.check("ip:a")[0] is False
    assert limiter.check("ip:b")[0] is True


def test_ttl_eviction_evicts_stale_keys() -> None:
    """Idle-ключи (last_touch > now - ttl) удаляются на cleanup-tick."""
    clock = FakeClock()
    limiter = RateLimiter(
        rpm=60,
        burst=1,
        key_ttl_sec=10.0,
        cleanup_every=2,  # сделаем cleanup максимально частым
        clock_fn=clock,
    )
    limiter.check("ip:stale")
    assert limiter.active_keys == 1
    # Проматываем за TTL и делаем cleanup-trigger вторым запросом.
    clock.advance(100.0)
    limiter.check("ip:fresh")
    # cleanup_every=2 → 2-й запрос триггерит eviction; ip:stale пропадает.
    assert "ip:stale" not in limiter._buckets
    assert "ip:fresh" in limiter._buckets


def test_classify_ip_buckets() -> None:
    """classify_ip различает localhost / lan / wan / unknown."""
    assert classify_ip("127.0.0.1") == "localhost"
    assert classify_ip("::1") == "localhost"
    assert classify_ip("192.168.1.10") == "lan"
    assert classify_ip("10.0.0.5") == "lan"
    assert classify_ip("8.8.8.8") == "wan"
    assert classify_ip("not-an-ip") == "unknown"
    assert classify_ip(None) == "unknown"


def test_is_rate_limit_enabled_env_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_rate_limit_enabled читает KRAB_RATE_LIMIT_ENABLED."""
    monkeypatch.delenv("KRAB_RATE_LIMIT_ENABLED", raising=False)
    assert is_rate_limit_enabled() is False
    monkeypatch.setenv("KRAB_RATE_LIMIT_ENABLED", "1")
    assert is_rate_limit_enabled() is True
    monkeypatch.setenv("KRAB_RATE_LIMIT_ENABLED", "0")
    assert is_rate_limit_enabled() is False


# ── ASGI integration via TestClient ───────────────────────────────────────────


def _build_app(limiter: RateLimiter) -> FastAPI:
    """Мини-FastAPI с middleware и парой endpoints для проверки HTTP-поведения."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/echo")
    async def echo() -> PlainTextResponse:  # noqa: D401
        return PlainTextResponse("ok")

    @app.get("/health")
    async def health() -> PlainTextResponse:  # noqa: D401
        return PlainTextResponse("ok")

    return app


def test_middleware_returns_429_with_retry_after_header() -> None:
    """После burst-исчерпания middleware отдаёт 429 + Retry-After header."""
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, burst=2, clock_fn=clock)
    client = TestClient(_build_app(limiter))
    # burst=2 → 2 OK, потом 429.
    assert client.get("/api/echo").status_code == 200
    assert client.get("/api/echo").status_code == 200
    resp = client.get("/api/echo")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 1
    body = resp.json()
    assert body.get("error") == "rate_limited"


def test_exempt_path_not_rate_limited() -> None:
    """Endpoint из EXEMPT_PATHS не учитывается лимитом даже при шквале."""
    assert "/health" in EXEMPT_PATHS
    clock = FakeClock()
    limiter = RateLimiter(rpm=60, burst=1, clock_fn=clock)
    client = TestClient(_build_app(limiter))
    # 50 раз health подряд — все 200.
    for _ in range(50):
        assert client.get("/health").status_code == 200
    # Bucket вообще не должен был создаться для health.
    # (TestClient ходит с одного IP, но exempt-ветка возвращается до limiter.check.)
    assert limiter.active_keys == 0
