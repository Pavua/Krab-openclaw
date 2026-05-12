# -*- coding: utf-8 -*-
"""Wave 153: тесты оптимизации probe ladder timeouts + paid_gemini_guard skip.

Контекст:
- Wave 149 smoke test поймал 29 network timeouts (endpoint latency > 15s),
  включая ``/api/openclaw/cloud/runtime-check`` 7-30s. Root cause:
  ``probe_gemini_key`` имел default httpx timeout=12s + _PROBE_OUTER_TIMEOUT=15s,
  и при включённом ``paid_gemini_guard`` (Wave 67) каждый probe всё равно бил по
  ``generativelanguage.googleapis.com`` и блокировался хуком.
- Wave 153 fix:
    * default httpx timeout → 5.0s,
    * _PROBE_OUTER_TIMEOUT → 6.0s,
    * skip probe entirely если guard mode == 'block' → возвращаем
      ``provider_status='blocked'``, ``semantic_error_code='blocked_by_guard'``.

Что проверяем (7 тестов):
  1. timeout-константы выставлены на ожидаемые "fast" значения.
  2. default keyword timeout = _PROBE_HTTPX_DEFAULT_TIMEOUT в signature.
  3. paid_gemini_guard block mode → skip без HTTP (быстрый return).
  4. paid_gemini_guard off mode → пропускает probe (HTTP идёт нормально).
  5. paid_gemini_guard warn mode → пропускает probe (HTTP идёт нормально).
  6. blocked fallback shape (все обязательные поля + to_dict сериализуется).
  7. PaidGeminiGuardError из _do_probe → возвращается blocked fallback (defence in depth).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest

from src.core import cloud_key_probe
from src.core.cloud_key_probe import (
    _PROBE_HTTPX_DEFAULT_TIMEOUT,
    _PROBE_OUTER_TIMEOUT,
    CloudProbeResult,
    _do_probe,
    _paid_gemini_guard_blocks_probe,
    probe_gemini_key,
)

# Валидный формат AI Studio key (AIza...)
_FAKE_KEY = "AIzaFakeKey1234567890abcdef1234"


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    """Чистим cache между тестами, чтобы не было кросс-теста загрязнения."""
    cloud_key_probe._probe_ok_cache.clear()
    yield
    cloud_key_probe._probe_ok_cache.clear()


# ---------------------------------------------------------------------------
# 1. timeout-константы выставлены на ожидаемые fast values
# ---------------------------------------------------------------------------


def test_probe_outer_timeout_is_short_enough_for_dashboard():
    """_PROBE_OUTER_TIMEOUT должен быть <= 6s, чтобы dashboard не зависал.

    Wave 149: smoke test поймал endpoints latency > 15s. Wave 153 cap → 6s.
    """
    assert _PROBE_OUTER_TIMEOUT <= 6.0, (
        f"_PROBE_OUTER_TIMEOUT={_PROBE_OUTER_TIMEOUT}s слишком большой; ожидаем <= 6s"
    )
    # И не настолько маленький, чтобы рвать живые сети
    assert _PROBE_OUTER_TIMEOUT >= 3.0


def test_probe_httpx_default_timeout_is_short():
    """Внутренний httpx timeout default должен быть в 3-5s диапазоне."""
    assert _PROBE_HTTPX_DEFAULT_TIMEOUT <= 5.0
    assert _PROBE_HTTPX_DEFAULT_TIMEOUT >= 3.0


# ---------------------------------------------------------------------------
# 2. default keyword timeout in signature
# ---------------------------------------------------------------------------


def test_probe_gemini_key_default_timeout_uses_module_constant():
    """Default ``timeout=`` kwarg должен быть _PROBE_HTTPX_DEFAULT_TIMEOUT."""
    sig = inspect.signature(probe_gemini_key)
    timeout_default = sig.parameters["timeout"].default
    assert timeout_default == _PROBE_HTTPX_DEFAULT_TIMEOUT, (
        f"default timeout = {timeout_default}, ожидали {_PROBE_HTTPX_DEFAULT_TIMEOUT}"
    )
    # Жёсткий cap: не должен превышать 5s
    assert timeout_default <= 5.0


# ---------------------------------------------------------------------------
# 3. paid_gemini_guard block mode → skip probe без HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_skipped_when_guard_blocks(monkeypatch):
    """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=1 → probe возвращает blocked без _do_probe."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")

    mock_do_probe = AsyncMock()
    with patch.object(cloud_key_probe, "_do_probe", mock_do_probe):
        result = await probe_gemini_key(
            _FAKE_KEY,
            key_source="env:GEMINI_API_KEY_PAID",
            key_tier="paid",
        )

    assert result.provider_status == "blocked"
    assert result.semantic_error_code == "blocked_by_guard"
    assert result.recovery_action == "use_vertex_or_disable_guard"
    # HTTP probe не должен был вызываться
    mock_do_probe.assert_not_awaited()


# ---------------------------------------------------------------------------
# 4. paid_gemini_guard off mode → пропускает probe (HTTP идёт)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_runs_when_guard_disabled(monkeypatch):
    """KRAB_BLOCK_PAID_GEMINI_AI_STUDIO=0 → _do_probe вызывается нормально."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")

    ok_result = CloudProbeResult(
        provider_status="ok",
        key_source="test",
        key_tier="free",
        semantic_error_code="ok",
        recovery_action="none",
        http_status=200,
        detail="",
    )

    async def fake_probe(*args, **kwargs) -> CloudProbeResult:
        return ok_result

    with patch.object(cloud_key_probe, "_do_probe", side_effect=fake_probe):
        result = await probe_gemini_key(
            _FAKE_KEY,
            key_source="test",
            key_tier="free",
        )

    # Когда guard off — реальный _do_probe вызывается, возвращает свой результат
    assert result.provider_status == "ok"
    assert result.semantic_error_code != "blocked_by_guard"


# ---------------------------------------------------------------------------
# 5. paid_gemini_guard warn mode → пропускает probe (HTTP идёт)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_runs_when_guard_in_warn_mode(monkeypatch):
    """warn mode не блокирует probe (только логирует)."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "warn")

    ok_result = CloudProbeResult(
        provider_status="ok",
        key_source="test",
        key_tier="free",
        semantic_error_code="ok",
        recovery_action="none",
        http_status=200,
        detail="",
    )

    async def fake_probe(*args, **kwargs) -> CloudProbeResult:
        return ok_result

    with patch.object(cloud_key_probe, "_do_probe", side_effect=fake_probe):
        result = await probe_gemini_key(
            _FAKE_KEY,
            key_source="test",
            key_tier="free",
        )

    assert result.semantic_error_code != "blocked_by_guard"
    # warn mode → guard logs warning, но pass-through
    assert _paid_gemini_guard_blocks_probe("free") is False


# ---------------------------------------------------------------------------
# 6. blocked fallback shape (поля и сериализация)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_fallback_has_expected_shape(monkeypatch):
    """Fallback должен иметь все обязательные поля и сериализоваться в dict."""
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "1")

    result = await probe_gemini_key(
        _FAKE_KEY,
        key_source="env:GEMINI_API_KEY_PAID",
        key_tier="paid",
    )

    # Базовый contract
    assert result.provider_status == "blocked"
    assert result.semantic_error_code == "blocked_by_guard"
    assert result.recovery_action == "use_vertex_or_disable_guard"
    assert result.key_source == "env:GEMINI_API_KEY_PAID"
    assert result.key_tier == "paid"
    assert result.http_status is None
    assert "paid_gemini_guard" in result.detail.lower()

    # to_dict сохраняет shape для JSON-ответов /api/openclaw/cloud/*
    payload = result.to_dict()
    assert payload["provider_status"] == "blocked"
    assert payload["semantic_error_code"] == "blocked_by_guard"
    assert payload["key_tier"] == "paid"
    # Все необходимые поля для UI присутствуют
    for required_field in (
        "provider_status",
        "key_source",
        "key_tier",
        "semantic_error_code",
        "recovery_action",
        "http_status",
        "detail",
    ):
        assert required_field in payload


# ---------------------------------------------------------------------------
# 7. PaidGeminiGuardError из _do_probe → blocked fallback (defence in depth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paid_gemini_guard_error_in_do_probe_returns_blocked(monkeypatch):
    """Если PaidGeminiGuardError всё-таки прорастает из httpx hook — ловим в _do_probe."""
    # Guard disabled, чтобы не сработал ранний skip; ошибка прилетит из httpx event_hook.
    monkeypatch.setenv("KRAB_BLOCK_PAID_GEMINI_AI_STUDIO", "0")

    from src.integrations.paid_gemini_guard import PaidGeminiGuardError

    class _RaisingClient:
        """Заглушка AsyncClient, который сразу raise PaidGeminiGuardError."""

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            raise PaidGeminiGuardError(
                url="https://generativelanguage.googleapis.com/v1beta/models",
                model="",
            )

        async def post(self, *args, **kwargs):  # pragma: no cover — недостижимо
            raise PaidGeminiGuardError(
                url="https://generativelanguage.googleapis.com/v1beta/models",
                model="",
            )

    with patch.object(cloud_key_probe.httpx, "AsyncClient", _RaisingClient):
        result = await _do_probe(
            _FAKE_KEY,
            key_source="env:test",
            key_tier="free",
            timeout=5.0,
            model="gemini-2.5-flash",
        )

    assert result.provider_status == "blocked"
    assert result.semantic_error_code == "blocked_by_guard"
    assert result.recovery_action == "use_vertex_or_disable_guard"
    assert result.key_tier == "free"
