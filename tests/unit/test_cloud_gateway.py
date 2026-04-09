# -*- coding: utf-8 -*-
"""
Тесты облачного шлюза `src/core/cloud_gateway.py`.

Покрытие:
1. classify_gemini_error — классификация по HTTP-коду и исключению.
2. get_cloud_fallback_chain — сборка цепочки по тирам, дедупликация.
3. fetch_google_models — парсинг ответа API, обработка ошибок.
4. verify_gemini_access — проверка доступа с моками.
5. resolve_working_gemini_key — приоритет free > paid, кеширование.
6. fetch_google_models_with_fallback — fallback free -> paid.
7. get_best_cloud_model — выбор модели по config/chain/verify.
"""
from __future__ import annotations

import httpx
import pytest

from src.core.cloud_gateway import (
    CLOUD_TIER_1_IDS,
    CLOUD_TIER_2_IDS,
    CLOUD_TIER_3_IDS,
    DEFAULT_CLOUD_MODEL,
    CloudErrorKind,
    classify_gemini_error,
    fetch_google_models,
    fetch_google_models_with_fallback,
    get_best_cloud_model,
    get_cloud_fallback_chain,
    resolve_working_gemini_key,
    verify_gemini_access,
)
from src.core.model_types import ModelInfo, ModelStatus, ModelType

# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

# Валидный AI Studio ключ (формат AIza..., длина >= 30)
FAKE_KEY = "AIzaSyD_fake_key_for_unit_tests_0123456789"
FAKE_KEY_2 = "AIzaSyD_second_fake_key_unit_tests_9876543"
BAD_KEY = "sk-not-an-aistudio-key"


def _make_gemini_list_response(model_names: list[str]) -> dict:
    """Генерирует payload /v1beta/models с указанными именами."""
    return {
        "models": [
            {"name": f"models/{n}", "displayName": n.replace("-", " ").title()}
            for n in model_names
        ]
    }


# ---------------------------------------------------------------------------
# 1. classify_gemini_error
# ---------------------------------------------------------------------------


class TestClassifyGeminiError:
    """Классификация ошибок Gemini по коду и исключению."""

    def test_auth_401(self) -> None:
        assert classify_gemini_error(status_code=401) == CloudErrorKind.AUTH

    def test_auth_403(self) -> None:
        assert classify_gemini_error(status_code=403) == CloudErrorKind.AUTH

    def test_quota_429(self) -> None:
        assert classify_gemini_error(status_code=429) == CloudErrorKind.QUOTA

    def test_timeout_408(self) -> None:
        assert classify_gemini_error(status_code=408) == CloudErrorKind.TIMEOUT

    def test_unknown_500(self) -> None:
        # 500 не имеет специальной категории — UNKNOWN
        assert classify_gemini_error(status_code=500) == CloudErrorKind.UNKNOWN

    def test_unknown_200(self) -> None:
        assert classify_gemini_error(status_code=200) == CloudErrorKind.UNKNOWN

    def test_timeout_exception(self) -> None:
        exc = httpx.ReadTimeout("read timed out")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.TIMEOUT

    def test_builtin_timeout_error(self) -> None:
        exc = TimeoutError("operation timed out")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.TIMEOUT

    def test_connect_error(self) -> None:
        exc = httpx.ConnectError("connection refused")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.NETWORK

    def test_os_error(self) -> None:
        exc = OSError("network down")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.NETWORK

    def test_none_args(self) -> None:
        assert classify_gemini_error() == CloudErrorKind.UNKNOWN

    def test_status_code_takes_precedence(self) -> None:
        # Если есть и код 429, и ConnectError — код приоритетнее
        exc = httpx.ConnectError("connection refused")
        assert classify_gemini_error(status_code=429, exc=exc) == CloudErrorKind.QUOTA


# ---------------------------------------------------------------------------
# 2. get_cloud_fallback_chain
# ---------------------------------------------------------------------------


class TestGetCloudFallbackChain:
    """Сборка цепочки облачных моделей по тирам."""

    def test_default_chain_contains_all_tiers(self) -> None:
        chain = get_cloud_fallback_chain()
        for mid in CLOUD_TIER_1_IDS + CLOUD_TIER_2_IDS + CLOUD_TIER_3_IDS:
            assert mid in chain

    def test_default_chain_no_duplicates(self) -> None:
        chain = get_cloud_fallback_chain()
        assert len(chain) == len(set(chain))

    def test_tier_order_preserved(self) -> None:
        # tier_1 идёт перед tier_2
        chain = get_cloud_fallback_chain()
        idx_t1_last = max(chain.index(m) for m in CLOUD_TIER_1_IDS)
        idx_t2_first = min(chain.index(m) for m in CLOUD_TIER_2_IDS)
        assert idx_t1_last < idx_t2_first

    def test_custom_tiers(self) -> None:
        chain = get_cloud_fallback_chain(
            tier_1=["a/model-1"],
            tier_2=["b/model-2"],
            tier_3=["c/model-3"],
            default="a/model-1",  # уже в цепочке — не дублируется
        )
        assert chain == ["a/model-1", "b/model-2", "c/model-3"]

    def test_dedup_across_tiers(self) -> None:
        # Дубликат между tier_1 и tier_2 — появляется один раз
        chain = get_cloud_fallback_chain(
            tier_1=["google/gemini-flash"],
            tier_2=["google/gemini-flash", "google/gemini-pro"],
            tier_3=[],
            default="google/gemini-flash",
        )
        assert chain == ["google/gemini-flash", "google/gemini-pro"]

    def test_default_model_appended_if_missing(self) -> None:
        chain = get_cloud_fallback_chain(
            tier_1=["a/x"], tier_2=[], tier_3=[], default="z/special"
        )
        assert chain[-1] == "z/special"

    def test_default_model_not_duplicated(self) -> None:
        chain = get_cloud_fallback_chain(
            tier_1=["google/gemini-2.5-flash"],
            tier_2=[],
            tier_3=[],
            default="google/gemini-2.5-flash",
        )
        assert chain.count("google/gemini-2.5-flash") == 1


# ---------------------------------------------------------------------------
# 3. fetch_google_models (async + httpx mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_google_models_no_key() -> None:
    """Без ключа — пустой список."""
    async with httpx.AsyncClient() as client:
        result = await fetch_google_models(None, client, models_cache={})
    assert result == []


@pytest.mark.asyncio
async def test_fetch_google_models_success() -> None:
    """Успешный парсинг списка gemini-моделей."""
    payload = _make_gemini_list_response(["gemini-2.5-flash", "gemini-pro", "text-bison"])

    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=payload)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        cache: dict[str, ModelInfo] = {}
        result = await fetch_google_models(FAKE_KEY, client, models_cache=cache)

    # text-bison не содержит "gemini" — отфильтрован
    ids = [m.id for m in result]
    assert "google/gemini-2.5-flash" in ids
    assert "google/gemini-pro" in ids
    assert "google/text-bison" not in ids
    # Кеш обновлён
    assert "google/gemini-2.5-flash" in cache


@pytest.mark.asyncio
async def test_fetch_google_models_error_status() -> None:
    """HTTP 403 — пустой список, диагностика записана."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(403, text="Forbidden")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        diag: list[dict] = []
        result = await fetch_google_models(
            FAKE_KEY, client, models_cache={}, diagnostics_sink=diag
        )
    assert result == []
    assert len(diag) == 1
    assert diag[0]["error_kind"] == "auth"
    assert diag[0]["status_code"] == 403


@pytest.mark.asyncio
async def test_fetch_google_models_network_error() -> None:
    """Сетевая ошибка — пустой список, диагностика записана."""

    def _raise(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(_raise)
    async with httpx.AsyncClient(transport=transport) as client:
        diag: list[dict] = []
        result = await fetch_google_models(
            FAKE_KEY, client, models_cache={}, diagnostics_sink=diag
        )
    assert result == []
    assert len(diag) == 1
    assert diag[0]["error_kind"] == "network"


@pytest.mark.asyncio
async def test_fetch_google_models_vision_detection() -> None:
    """flash/pro модели помечаются supports_vision=True."""
    payload = _make_gemini_list_response(["gemini-2.5-flash", "gemini-1.0-nano"])
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=payload)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_google_models(FAKE_KEY, client, models_cache={})

    flash = next(m for m in result if "flash" in m.id)
    assert flash.supports_vision is True
    assert flash.type == ModelType.CLOUD_GEMINI
    assert flash.status == ModelStatus.AVAILABLE


# ---------------------------------------------------------------------------
# 4. verify_gemini_access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_gemini_access_no_key() -> None:
    async with httpx.AsyncClient() as client:
        assert await verify_gemini_access("gemini-2.5-flash", None, client) is False


@pytest.mark.asyncio
async def test_verify_gemini_access_success() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert await verify_gemini_access("google/gemini-2.5-flash", FAKE_KEY, client) is True


@pytest.mark.asyncio
async def test_verify_gemini_access_forbidden() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(403, text="Forbidden")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert await verify_gemini_access("google/gemini-2.5-flash", FAKE_KEY, client) is False


@pytest.mark.asyncio
async def test_verify_gemini_access_network_error() -> None:
    def _raise(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(_raise)
    async with httpx.AsyncClient(transport=transport) as client:
        assert await verify_gemini_access("google/gemini-2.5-flash", FAKE_KEY, client) is False


# ---------------------------------------------------------------------------
# 5. resolve_working_gemini_key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_working_gemini_key_free_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """free-ключ работает — paid не проверяется."""

    # verify_gemini_access мокаем — free вернёт True
    call_log: list[str] = []

    async def _mock_verify(model_id: str, key: str | None, client, **kw) -> bool:
        call_log.append(key or "")
        return key == FAKE_KEY

    monkeypatch.setattr("src.core.cloud_gateway.verify_gemini_access", _mock_verify)

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_working_gemini_key(
            FAKE_KEY, FAKE_KEY_2, client,
            _cache={},  # изолированный кеш
        )
    assert result == FAKE_KEY
    # paid не должен проверяться
    assert FAKE_KEY_2 not in call_log


@pytest.mark.asyncio
async def test_resolve_working_gemini_key_fallback_to_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """free не работает — fallback на paid."""

    async def _mock_verify(model_id: str, key: str | None, client, **kw) -> bool:
        return key == FAKE_KEY_2

    monkeypatch.setattr("src.core.cloud_gateway.verify_gemini_access", _mock_verify)

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_working_gemini_key(
            FAKE_KEY, FAKE_KEY_2, client,
            _cache={},
        )
    assert result == FAKE_KEY_2


@pytest.mark.asyncio
async def test_resolve_working_gemini_key_none_if_all_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Оба ключа не работают — None."""

    async def _mock_verify(model_id: str, key: str | None, client, **kw) -> bool:
        return False

    monkeypatch.setattr("src.core.cloud_gateway.verify_gemini_access", _mock_verify)

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_working_gemini_key(
            FAKE_KEY, FAKE_KEY_2, client,
            _cache={},
        )
    assert result is None


@pytest.mark.asyncio
async def test_resolve_working_gemini_key_bad_format_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключ с неверным форматом пропускается."""

    async def _mock_verify(model_id: str, key: str | None, client, **kw) -> bool:
        return True  # если дойдём до verify — вернём True

    monkeypatch.setattr("src.core.cloud_gateway.verify_gemini_access", _mock_verify)

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await resolve_working_gemini_key(
            BAD_KEY, FAKE_KEY_2, client,
            _cache={},
        )
    # BAD_KEY пропущен (формат), FAKE_KEY_2 прошёл
    assert result == FAKE_KEY_2


# ---------------------------------------------------------------------------
# 6. fetch_google_models_with_fallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_google_models_with_fallback_free_success() -> None:
    """free-ключ возвращает модели — paid не используется."""
    payload = _make_gemini_list_response(["gemini-2.5-flash"])

    call_count = 0

    def _handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await fetch_google_models_with_fallback(
            FAKE_KEY, FAKE_KEY_2, client, models_cache={}
        )
    assert len(result) == 1
    assert result[0].id == "google/gemini-2.5-flash"
    # Один вызов — paid не трогали
    assert call_count == 1


@pytest.mark.asyncio
async def test_fetch_google_models_with_fallback_bad_keys() -> None:
    """Оба ключа невалидного формата — пустой список, diagnostics."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        diag: list[dict] = []
        result = await fetch_google_models_with_fallback(
            BAD_KEY, "short", client, models_cache={}, diagnostics_sink=diag
        )
    assert result == []
    assert len(diag) == 2
    assert all(d["error_kind"] == "invalid" for d in diag)


# ---------------------------------------------------------------------------
# 7. get_best_cloud_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_best_cloud_model_config_override() -> None:
    """Если config_model != 'auto' — возвращается как есть."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await get_best_cloud_model(
            FAKE_KEY, client, config_model="my/custom-model"
        )
    assert result == "my/custom-model"


@pytest.mark.asyncio
async def test_get_best_cloud_model_no_key_returns_default() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await get_best_cloud_model(None, client)
    assert result == DEFAULT_CLOUD_MODEL


@pytest.mark.asyncio
async def test_get_best_cloud_model_auto_with_verify() -> None:
    """auto + verify_fn — проверяет модели по цепочке."""

    async def _verify(model_id: str, key: str | None, client) -> bool:
        return "pro" in model_id  # только pro работает

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await get_best_cloud_model(
            FAKE_KEY, client,
            config_model="auto",
            fallback_chain=["google/gemini-flash", "google/gemini-pro"],
            verify_fn=_verify,
        )
    assert result == "google/gemini-pro"


@pytest.mark.asyncio
async def test_get_best_cloud_model_auto_no_verify() -> None:
    """auto без verify_fn — первый gemini из цепочки."""
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await get_best_cloud_model(
            FAKE_KEY, client,
            config_model="auto",
            fallback_chain=["google/gemini-2.5-flash", "google/gemini-pro"],
        )
    assert result == "google/gemini-2.5-flash"


@pytest.mark.asyncio
async def test_get_best_cloud_model_verify_all_fail() -> None:
    """Все модели не проходят verify — fallback на DEFAULT_CLOUD_MODEL."""

    async def _verify(model_id: str, key: str | None, client) -> bool:
        return False

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        # config_model=None чтобы проверить чистый fallback на DEFAULT
        result = await get_best_cloud_model(
            FAKE_KEY, client,
            config_model=None,
            fallback_chain=["google/gemini-flash", "google/gemini-pro"],
            verify_fn=_verify,
        )
    assert result == DEFAULT_CLOUD_MODEL
