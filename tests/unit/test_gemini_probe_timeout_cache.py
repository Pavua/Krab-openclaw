# -*- coding: utf-8 -*-
"""
Тесты: timeout и TTL-кеш probe_gemini_key.

Проверяем:
- asyncio.TimeoutError → статус probe_timeout (не network_error)
- probe_timeout не записывает network_error в cloud tier state
- Успешный результат кешируется на TTL
- После истечения TTL — новый probe
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from src.core import cloud_key_probe
from src.core.cloud_key_probe import (
    _PROBE_CACHE_TTL,
    _PROBE_OUTER_TIMEOUT,
    CloudProbeResult,
    probe_gemini_key,
)

# Валидный тестовый ключ (формат AIza...)
_FAKE_KEY = "AIzaFakeKey1234567890abcdef1234"


def _ok_result(key_source: str = "test", key_tier: str = "free") -> CloudProbeResult:
    return CloudProbeResult(
        provider_status="ok",
        key_source=key_source,
        key_tier=key_tier,
        semantic_error_code="ok",
        recovery_action="none",
        http_status=200,
        detail="",
    )


@pytest.fixture(autouse=True)
def clear_probe_cache() -> None:
    """Очищаем кеш перед каждым тестом."""
    cloud_key_probe._probe_ok_cache.clear()
    yield
    cloud_key_probe._probe_ok_cache.clear()


# ---------------------------------------------------------------------------
# Тест 1: TimeoutError → probe_timeout, не network_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_timeout_returns_probe_timeout_not_network_error() -> None:
    """При зависании _do_probe — возвращаем probe_timeout, не network_error."""
    async def slow_probe(*args, **kwargs):
        await asyncio.sleep(100)  # зависает

    with patch.object(cloud_key_probe, "_do_probe", side_effect=slow_probe):
        with patch.object(cloud_key_probe, "_PROBE_OUTER_TIMEOUT", 0.05):
            result = await probe_gemini_key(
                _FAKE_KEY,
                key_source="test",
                key_tier="free",
            )

    assert result.provider_status == "timeout"
    assert result.semantic_error_code == "probe_timeout"
    assert result.semantic_error_code != "network_error"


@pytest.mark.asyncio
async def test_probe_timeout_does_not_populate_cache() -> None:
    """Timeout не должен записывать результат в кеш."""
    async def slow_probe(*args, **kwargs):
        await asyncio.sleep(100)

    with patch.object(cloud_key_probe, "_do_probe", side_effect=slow_probe):
        with patch.object(cloud_key_probe, "_PROBE_OUTER_TIMEOUT", 0.05):
            await probe_gemini_key(_FAKE_KEY, key_source="test", key_tier="free")

    cache_key = (_FAKE_KEY, "test", "free")
    assert cache_key not in cloud_key_probe._probe_ok_cache


# ---------------------------------------------------------------------------
# Тест 2: Успешный результат кешируется и возвращается без нового HTTP-вызова
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_successful_probe_is_cached() -> None:
    """После успешного probe второй вызов берёт результат из кеша."""
    ok = _ok_result()
    call_count = 0

    async def mock_do_probe(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return ok

    with patch.object(cloud_key_probe, "_do_probe", side_effect=mock_do_probe):
        r1 = await probe_gemini_key(_FAKE_KEY, key_source="test", key_tier="free")
        r2 = await probe_gemini_key(_FAKE_KEY, key_source="test", key_tier="free")

    assert r1.provider_status == "ok"
    assert r2.provider_status == "ok"
    # Второй вызов — из кеша, _do_probe должен быть вызван только 1 раз
    assert call_count == 1


# ---------------------------------------------------------------------------
# Тест 3: После истечения TTL — новый probe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_cache_triggers_new_probe() -> None:
    """Если TTL истёк — должен быть новый HTTP-вызов."""
    ok = _ok_result()
    call_count = 0

    async def mock_do_probe(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return ok

    # Вручную записываем в кеш с истёкшим timestamp
    expired_at = time.monotonic() - (_PROBE_CACHE_TTL + 5)
    cache_key = (_FAKE_KEY, "test", "free")
    cloud_key_probe._probe_ok_cache[cache_key] = (ok, expired_at)

    with patch.object(cloud_key_probe, "_do_probe", side_effect=mock_do_probe):
        result = await probe_gemini_key(_FAKE_KEY, key_source="test", key_tier="free")

    assert result.provider_status == "ok"
    # Истёкший кеш → новый probe → count == 1
    assert call_count == 1


# ---------------------------------------------------------------------------
# Тест 4: probe_timeout не влияет на _cloud_tier_state как network_error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probe_timeout_semantic_code_is_probe_timeout() -> None:
    """semantic_error_code при таймауте строго 'probe_timeout'."""
    async def slow_probe(*args, **kwargs):
        await asyncio.sleep(100)

    with patch.object(cloud_key_probe, "_do_probe", side_effect=slow_probe):
        with patch.object(cloud_key_probe, "_PROBE_OUTER_TIMEOUT", 0.05):
            result = await probe_gemini_key(_FAKE_KEY, key_source="env:test", key_tier="paid")

    assert result.semantic_error_code == "probe_timeout"
    assert "network_error" not in result.semantic_error_code
    assert result.recovery_action == "retry_later"
    assert str(_PROBE_OUTER_TIMEOUT) in result.detail or "15" in result.detail or "timed out" in result.detail


# ---------------------------------------------------------------------------
# Тест 5: missing key не затрагивает кеш
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_key_returns_missing_without_cache() -> None:
    """Пустой ключ → provider_status missing, кеш не трогаем."""
    result = await probe_gemini_key(None, key_source="env:NONE", key_tier="free")
    assert result.provider_status == "missing"
    assert result.semantic_error_code == "missing_api_key"
    assert len(cloud_key_probe._probe_ok_cache) == 0


# ---------------------------------------------------------------------------
# Тест 6: network_error из _do_probe НЕ кешируется
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_network_error_not_cached() -> None:
    """network_error из _do_probe не должен попасть в кеш."""
    err_result = CloudProbeResult(
        provider_status="error",
        key_source="test",
        key_tier="free",
        semantic_error_code="network_error",
        recovery_action="retry_or_fallback",
        detail="connection refused",
    )

    async def mock_do_probe(*args, **kwargs):
        return err_result

    with patch.object(cloud_key_probe, "_do_probe", side_effect=mock_do_probe):
        result = await probe_gemini_key(_FAKE_KEY, key_source="test", key_tier="free")

    assert result.semantic_error_code == "network_error"
    # Ошибка не кешируется
    cache_key = (_FAKE_KEY, "test", "free")
    assert cache_key not in cloud_key_probe._probe_ok_cache
