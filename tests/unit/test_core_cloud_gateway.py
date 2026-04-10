# -*- coding: utf-8 -*-
"""Тесты для src/core/cloud_gateway.py — облачный шлюз, fallback, классификация ошибок."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
    get_best_cloud_model,
    get_cloud_fallback_chain,
    reset_gemini_key_cache,
    resolve_working_gemini_key,
    verify_gemini_access,
)
from src.core.model_types import ModelInfo, ModelStatus, ModelType

# ---------------------------------------------------------------------------
# classify_gemini_error
# ---------------------------------------------------------------------------


class TestClassifyGeminiError:
    """Классификация ошибок Gemini API."""

    def test_auth_401(self):
        assert classify_gemini_error(status_code=401) == CloudErrorKind.AUTH

    def test_auth_403(self):
        assert classify_gemini_error(status_code=403) == CloudErrorKind.AUTH

    def test_quota_429(self):
        assert classify_gemini_error(status_code=429) == CloudErrorKind.QUOTA

    def test_timeout_408(self):
        assert classify_gemini_error(status_code=408) == CloudErrorKind.TIMEOUT

    def test_timeout_exception(self):
        exc = httpx.TimeoutException("read timed out")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.TIMEOUT

    def test_timeout_builtin(self):
        assert classify_gemini_error(exc=TimeoutError()) == CloudErrorKind.TIMEOUT

    def test_network_connect_error(self):
        exc = httpx.ConnectError("connection refused")
        assert classify_gemini_error(exc=exc) == CloudErrorKind.NETWORK

    def test_network_os_error(self):
        assert classify_gemini_error(exc=OSError("no route")) == CloudErrorKind.NETWORK

    def test_unknown_status(self):
        """Неизвестный HTTP-код -> UNKNOWN."""
        assert classify_gemini_error(status_code=500) == CloudErrorKind.UNKNOWN

    def test_unknown_no_args(self):
        assert classify_gemini_error() == CloudErrorKind.UNKNOWN

    def test_status_takes_priority_over_exc(self):
        """Если status_code 403 и exc=TimeoutError, AUTH побеждает (status проверяется первым)."""
        result = classify_gemini_error(status_code=403, exc=TimeoutError())
        assert result == CloudErrorKind.AUTH


# ---------------------------------------------------------------------------
# get_cloud_fallback_chain
# ---------------------------------------------------------------------------


class TestGetCloudFallbackChain:
    """Построение fallback-цепочки облачных моделей."""

    def test_default_chain_contains_all_tiers(self):
        chain = get_cloud_fallback_chain()
        for mid in CLOUD_TIER_1_IDS + CLOUD_TIER_2_IDS + CLOUD_TIER_3_IDS:
            assert mid in chain

    def test_default_chain_no_duplicates(self):
        chain = get_cloud_fallback_chain()
        assert len(chain) == len(set(chain))

    def test_custom_tiers(self):
        # default модель всегда добавляется в конец если не в тирах
        chain = get_cloud_fallback_chain(tier_1=["a"], tier_2=["b"], tier_3=["c"])
        assert chain[:3] == ["a", "b", "c"]

    def test_dedup_across_tiers(self):
        chain = get_cloud_fallback_chain(tier_1=["x", "y"], tier_2=["y", "z"], tier_3=[])
        assert chain[:3] == ["x", "y", "z"]
        assert len(chain) == len(set(chain))

    def test_default_appended_if_missing(self):
        chain = get_cloud_fallback_chain(
            tier_1=["only-one"], tier_2=[], tier_3=[], default="fallback-model"
        )
        assert chain[-1] == "fallback-model"

    def test_default_not_duplicated(self):
        """Если default уже в цепочке, не дублируется."""
        chain = get_cloud_fallback_chain(
            tier_1=["my-default"], tier_2=[], tier_3=[], default="my-default"
        )
        assert chain.count("my-default") == 1

    def test_tier_order_preserved(self):
        """tier_1 идёт перед tier_2, tier_2 перед tier_3."""
        chain = get_cloud_fallback_chain()
        first_t1 = chain.index(CLOUD_TIER_1_IDS[0])
        first_t2 = chain.index(CLOUD_TIER_2_IDS[0])
        first_t3 = chain.index(CLOUD_TIER_3_IDS[0])
        assert first_t1 < first_t2 < first_t3


# ---------------------------------------------------------------------------
# fetch_google_models
# ---------------------------------------------------------------------------


class TestFetchGoogleModels:
    """Запрос списка моделей у Google Gemini API."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_empty(self):
        client = AsyncMock()
        result = await fetch_google_models(None, client, models_cache={})
        assert result == []
        client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_parses_models(self):
        """Успешный ответ — модели парсятся в ModelInfo."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "models": [
                {"name": "models/gemini-2.5-flash", "displayName": "Gemini 2.5 Flash"},
                {"name": "models/gemini-pro", "displayName": "Gemini Pro"},
                {"name": "models/text-bison-001", "displayName": "Text Bison"},  # не gemini
            ]
        }
        client = AsyncMock()
        client.get.return_value = response
        cache: dict[str, ModelInfo] = {}

        result = await fetch_google_models(
            "AIzaTestKey1234567890abcdefgh", client, models_cache=cache
        )

        # text-bison отфильтрован
        assert len(result) == 2
        assert result[0].id == "google/gemini-2.5-flash"
        assert result[0].type == ModelType.CLOUD_GEMINI
        assert result[0].status == ModelStatus.AVAILABLE
        # кэш заполнен
        assert "google/gemini-2.5-flash" in cache
        assert "google/gemini-pro" in cache

    @pytest.mark.asyncio
    async def test_non_200_returns_empty(self):
        response = MagicMock()
        response.status_code = 403
        response.text = "Forbidden"
        client = AsyncMock()
        client.get.return_value = response

        diag: list[dict] = []
        result = await fetch_google_models(
            "AIzaTestKey1234567890abcdefgh",
            client,
            models_cache={},
            diagnostics_sink=diag,
        )
        assert result == []
        assert len(diag) == 1
        assert diag[0]["error_kind"] == "auth"

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self):
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("refused")

        diag: list[dict] = []
        result = await fetch_google_models(
            "AIzaTestKey1234567890abcdefgh",
            client,
            models_cache={},
            diagnostics_sink=diag,
        )
        assert result == []
        assert diag[0]["error_kind"] == "network"

    @pytest.mark.asyncio
    async def test_json_parse_error(self):
        """Невалидный JSON в ответе."""
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = json.JSONDecodeError("bad", "", 0)
        client = AsyncMock()
        client.get.return_value = response

        diag: list[dict] = []
        result = await fetch_google_models(
            "AIzaTestKey1234567890abcdefgh",
            client,
            models_cache={},
            diagnostics_sink=diag,
        )
        assert result == []
        assert diag[0]["error_kind"] == "parse"

    @pytest.mark.asyncio
    async def test_empty_models_list(self):
        """Ответ 200, но models пустой."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"models": []}
        client = AsyncMock()
        client.get.return_value = response

        result = await fetch_google_models("AIzaTestKey1234567890abcdefgh", client, models_cache={})
        assert result == []

    @pytest.mark.asyncio
    async def test_diagnostics_sink_on_success(self):
        """При 200 в diagnostics_sink пишется ok."""
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"models": []}
        client = AsyncMock()
        client.get.return_value = response

        diag: list[dict] = []
        await fetch_google_models(
            "AIzaTestKey1234567890abcdefgh",
            client,
            models_cache={},
            diagnostics_sink=diag,
            key_tier="free",
            key_source="env",
        )
        assert diag[0]["error_kind"] == "ok"
        assert diag[0]["key_tier"] == "free"


# ---------------------------------------------------------------------------
# verify_gemini_access
# ---------------------------------------------------------------------------


class TestVerifyGeminiAccess:
    """Проверка доступа к конкретной Gemini-модели."""

    @pytest.mark.asyncio
    async def test_no_key_returns_false(self):
        assert await verify_gemini_access("gemini-2.5-flash", None, AsyncMock()) is False

    @pytest.mark.asyncio
    async def test_200_returns_true(self):
        response = MagicMock()
        response.status_code = 200
        client = AsyncMock()
        client.post.return_value = response
        assert await verify_gemini_access("google/gemini-2.5-flash", "key", client) is True

    @pytest.mark.asyncio
    async def test_403_returns_false(self):
        response = MagicMock()
        response.status_code = 403
        client = AsyncMock()
        client.post.return_value = response
        assert await verify_gemini_access("google/gemini-2.5-flash", "key", client) is False

    @pytest.mark.asyncio
    async def test_network_error_returns_false(self):
        client = AsyncMock()
        client.post.side_effect = httpx.ConnectError("refused")
        assert await verify_gemini_access("model", "key", client) is False


# ---------------------------------------------------------------------------
# resolve_working_gemini_key
# ---------------------------------------------------------------------------


class TestResolveWorkingGeminiKey:
    """Резолв рабочего API-ключа с кэшированием."""

    @pytest.mark.asyncio
    async def test_free_key_works(self):
        """Свободный ключ проходит — возвращается он."""
        client = AsyncMock()
        with (
            patch(
                "src.core.cloud_gateway.verify_gemini_access",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch("src.core.cloud_gateway.is_ai_studio_key", return_value=True),
        ):
            # передаём отдельный кэш, чтобы не загрязнять mutable default
            result = await resolve_working_gemini_key(
                "AIzaFreeKey1234567890abcdefgh",
                None,
                client,
                _cache={},
            )
        assert result == "AIzaFreeKey1234567890abcdefgh"

    @pytest.mark.asyncio
    async def test_falls_back_to_paid(self):
        """Свободный не работает — берём платный."""
        call_count = 0

        async def fake_verify(model_id, key, client, **kw):
            nonlocal call_count
            call_count += 1
            # первый вызов (free) — fail, второй (paid) — ok
            return call_count > 1

        client = AsyncMock()
        with (
            patch("src.core.cloud_gateway.verify_gemini_access", side_effect=fake_verify),
            patch("src.core.cloud_gateway.is_ai_studio_key", return_value=True),
        ):
            result = await resolve_working_gemini_key(
                "AIzaFree1234567890abcdefghijk",
                "AIzaPaid1234567890abcdefghijk",
                client,
                _cache={},
            )
        assert result == "AIzaPaid1234567890abcdefghijk"

    @pytest.mark.asyncio
    async def test_no_keys_returns_none(self):
        client = AsyncMock()
        result = await resolve_working_gemini_key(None, None, client, _cache={})
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Повторный вызов берёт из кэша."""
        client = AsyncMock()
        cache = {"resolved": "cached-key"}
        result = await resolve_working_gemini_key("free", "paid", client, _cache=cache)
        assert result == "cached-key"

    @pytest.mark.asyncio
    async def test_invalid_format_skipped(self):
        """Ключ невалидного формата пропускается."""
        client = AsyncMock()
        with patch("src.core.cloud_gateway.is_ai_studio_key", return_value=False):
            result = await resolve_working_gemini_key("badkey", "alsobad", client, _cache={})
        assert result is None


# ---------------------------------------------------------------------------
# reset_gemini_key_cache
# ---------------------------------------------------------------------------


class TestResetGeminiKeyCache:
    """Сброс кэша ключей."""

    def test_reset_clears_resolved(self):
        # _cache — keyword-only → __kwdefaults__
        kw = resolve_working_gemini_key.__kwdefaults__
        assert kw and "_cache" in kw, "_cache должен быть в keyword defaults"
        cache_dict = kw["_cache"]
        cache_dict["resolved"] = "old-key"
        reset_gemini_key_cache()
        assert "resolved" not in cache_dict


# ---------------------------------------------------------------------------
# get_best_cloud_model
# ---------------------------------------------------------------------------


class TestGetBestCloudModel:
    """Выбор лучшей облачной модели."""

    @pytest.mark.asyncio
    async def test_explicit_config_model(self):
        """Если config_model задан и не auto — возвращается он."""
        result = await get_best_cloud_model("key", AsyncMock(), config_model="my-model")
        assert result == "my-model"

    @pytest.mark.asyncio
    async def test_no_key_returns_default(self):
        result = await get_best_cloud_model(None, AsyncMock())
        assert result == DEFAULT_CLOUD_MODEL

    @pytest.mark.asyncio
    async def test_auto_with_key_returns_first_gemini(self):
        """config_model=auto с ключом — первая gemini из цепочки."""
        result = await get_best_cloud_model(
            "key",
            AsyncMock(),
            config_model="auto",
            fallback_chain=["google/gemini-2.5-flash", "google/gemini-pro"],
        )
        assert result == "google/gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_verify_fn_picks_second(self):
        """verify_fn отвергает первую модель, берёт вторую."""
        call_idx = 0

        async def fake_verify(model_id, key, client):
            nonlocal call_idx
            call_idx += 1
            return call_idx > 1  # первую отклоняет

        result = await get_best_cloud_model(
            "key",
            AsyncMock(),
            config_model="auto",
            fallback_chain=["google/gemini-a", "google/gemini-b"],
            verify_fn=fake_verify,
        )
        assert result == "google/gemini-b"

    @pytest.mark.asyncio
    async def test_verify_fn_all_fail(self):
        """Все модели не прошли verify -> config_model="auto" возвращается как есть."""

        async def always_fail(model_id, key, client):
            return False

        result = await get_best_cloud_model(
            "key",
            AsyncMock(),
            config_model="auto",
            fallback_chain=["google/gemini-a"],
            verify_fn=always_fail,
        )
        # "auto" truthy → возвращается config_model напрямую
        assert result == "auto"

    @pytest.mark.asyncio
    async def test_verify_fn_exception_skips(self):
        """Исключение в verify_fn — модель пропускается, не крашит."""

        async def exploding_verify(model_id, key, client):
            raise RuntimeError("boom")

        result = await get_best_cloud_model(
            "key",
            AsyncMock(),
            config_model="auto",
            fallback_chain=["google/gemini-a"],
            verify_fn=exploding_verify,
        )
        assert result == "auto"

    @pytest.mark.asyncio
    async def test_chain_without_gemini(self):
        """Цепочка без 'gemini' в названиях — возвращается default."""
        result = await get_best_cloud_model(
            "key",
            AsyncMock(),
            fallback_chain=["openai/gpt-4", "anthropic/claude"],
        )
        assert result == DEFAULT_CLOUD_MODEL
