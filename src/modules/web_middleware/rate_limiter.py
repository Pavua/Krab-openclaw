# -*- coding: utf-8 -*-
"""Wave 96: owner-panel per-IP token-bucket rate limiter.

Защищает FastAPI owner-панель `:8080` от случайного hammering
(stuck browser tabs, кривые watchdog-петли). По умолчанию 60 req/min с
burst 10 на ключ (IP). Endpoints `/health`-семейства exempt'нуты —
их дёргают мониторы каждые несколько секунд.

Архитектура:
    Token-bucket per-key, in-memory dict `{key: (tokens, last_refill)}`.
    Refill rate = rpm/60 токенов/сек. Bucket capacity = burst.
    Cleanup: idle ключи (не трогали > TTL) выселяются на каждом N-ом запросе.

Env-gate:
    KRAB_RATE_LIMIT_ENABLED=1       — включить middleware
    KRAB_RATE_LIMIT_RPM=60          — токенов/мин
    KRAB_RATE_LIMIT_BURST=10        — capacity bucket'а
    KRAB_RATE_LIMIT_KEY_TTL_SEC=600 — TTL idle-ключа

Тестируемость:
    `clock_fn` injection (default = time.monotonic) — позволяет
    юнит-тестам прокручивать время без `asyncio.sleep`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.metrics.rate_limit import record_block, set_active_keys

# Пути, освобождённые от лимита (мониторинг / healthcheck).
# Используются Cloudflare/Prometheus/локальным watchdog — высокая частота это нормально.
EXEMPT_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/healthz",
        "/metrics",
        "/api/health/lite",
        "/api/v1/health",
    }
)


class TokenBucket:
    """Простой token-bucket: capacity + refill_per_sec, monotonic clock."""

    __slots__ = ("capacity", "refill_per_sec", "tokens", "last_refill", "last_touch")

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        now: float,
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self.last_refill = now
        self.last_touch = now

    def try_consume(self, now: float, amount: float = 1.0) -> bool:
        """Пробует списать ``amount`` токенов. True если хватило."""
        # Дозалить bucket пропорционально прошедшему времени.
        elapsed = max(0.0, now - self.last_refill)
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self.last_refill = now
        self.last_touch = now
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False

    def retry_after_sec(self) -> float:
        """Секунд до того, как появится ≥1 токен (минимум 1с для Retry-After)."""
        if self.refill_per_sec <= 0:
            return 60.0
        deficit = max(0.0, 1.0 - self.tokens)
        return max(1.0, deficit / self.refill_per_sec)


class RateLimiter:
    """In-memory token-bucket storage с TTL-cleanup.

    Используется как обёрнутый state для middleware — выделено отдельно,
    чтобы юнит-тесты могли инстанцировать без FastAPI.
    """

    def __init__(
        self,
        rpm: int = 60,
        burst: int = 10,
        key_ttl_sec: float = 600.0,
        cleanup_every: int = 256,
        clock_fn: Callable[[], float] | None = None,
    ) -> None:
        self.rpm = int(rpm)
        self.burst = int(burst)
        self.key_ttl_sec = float(key_ttl_sec)
        self.cleanup_every = int(cleanup_every)
        self._clock: Callable[[], float] = clock_fn or time.monotonic
        self._buckets: dict[str, TokenBucket] = {}
        self._refill_per_sec: float = self.rpm / 60.0
        self._req_counter: int = 0

    @property
    def active_keys(self) -> int:
        """Текущее число live-ключей в стейте."""
        return len(self._buckets)

    def check(self, key: str) -> tuple[bool, float]:
        """Возвращает (allowed, retry_after_sec).

        ``retry_after_sec`` имеет смысл только при ``allowed=False``.
        """
        now = self._clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(
                capacity=self.burst,
                refill_per_sec=self._refill_per_sec,
                now=now,
            )
            self._buckets[key] = bucket
        allowed = bucket.try_consume(now)
        self._req_counter += 1
        if self._req_counter % self.cleanup_every == 0:
            self._evict_stale(now)
        set_active_keys(self.active_keys)
        if allowed:
            return True, 0.0
        return False, bucket.retry_after_sec()

    def _evict_stale(self, now: float) -> None:
        """Выбрасывает ключи, не тронутые > key_ttl_sec."""
        cutoff = now - self.key_ttl_sec
        # Копию ключей берём, чтобы не мутировать dict при итерации.
        stale = [k for k, b in self._buckets.items() if b.last_touch < cutoff]
        for k in stale:
            self._buckets.pop(k, None)


def _client_key(request: Request) -> str:
    """Извлекает ключ rate-limit'а.

    Приоритет:
      1. Authorization header (хэш первых 16 символов) — отдельный bucket per-token.
      2. ``X-Forwarded-For`` (первый IP) — за reverse-proxy (Cloudflare/nginx).
      3. ``request.client.host``.
      4. fallback ``"unknown"``.
    """
    auth = request.headers.get("Authorization", "")
    if auth:
        # Не логируем сам токен; используем короткий префикс для разнесения.
        return f"auth:{auth[:24]}"
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        ip = fwd.split(",", 1)[0].strip()
        if ip:
            return f"ip:{ip}"
    client = request.client
    if client is not None and client.host:
        return f"ip:{client.host}"
    return "ip:unknown"


def _client_ip(request: Request) -> str | None:
    """Голый IP клиента (без префикса) — для metrics-классификации."""
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        ip = fwd.split(",", 1)[0].strip()
        if ip:
            return ip
    if request.client is not None and request.client.host:
        return request.client.host
    return None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI-middleware: применяет token-bucket per-IP/token к owner-панели.

    Активна только если ``KRAB_RATE_LIMIT_ENABLED=1``; иначе middleware
    пропускает всё насквозь (no-op). EXEMPT_PATHS никогда не лимитируются.
    """

    def __init__(
        self,
        app: Any,
        limiter: RateLimiter | None = None,
        exempt_paths: frozenset[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or _limiter_from_env()
        self._exempt = exempt_paths if exempt_paths is not None else EXEMPT_PATHS

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in self._exempt:
            return await call_next(request)
        key = _client_key(request)
        allowed, retry_after = self._limiter.check(key)
        if allowed:
            return await call_next(request)
        record_block(path=path, ip=_client_ip(request))
        retry_int = int(round(retry_after)) or 1
        return Response(
            content='{"error":"rate_limited","retry_after":%d}' % retry_int,
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_int)},
        )


def _limiter_from_env() -> RateLimiter:
    """Собирает RateLimiter из env-переменных (KRAB_RATE_LIMIT_*)."""

    def _int_env(name: str, default: int) -> int:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _float_env(name: str, default: float) -> float:
        raw = os.getenv(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    return RateLimiter(
        rpm=_int_env("KRAB_RATE_LIMIT_RPM", 60),
        burst=_int_env("KRAB_RATE_LIMIT_BURST", 10),
        key_ttl_sec=_float_env("KRAB_RATE_LIMIT_KEY_TTL_SEC", 600.0),
    )


def is_rate_limit_enabled() -> bool:
    """True если KRAB_RATE_LIMIT_ENABLED=1 (cycled через env helper)."""
    return os.getenv("KRAB_RATE_LIMIT_ENABLED", "").strip() == "1"
