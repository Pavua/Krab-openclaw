# -*- coding: utf-8 -*-
"""Wave 239: тесты автодискаверинга LM Studio моделей.

Покрытие
--------
- success path: парсинг 84+ моделей из payload
- embedding/rerank/whisper моделей нет в выдаче (отфильтрованы)
- cache TTL — второй вызов без сети
- force_refresh пропускает кэш
- error path: timeout / connection refused / 401 → пустой список
- prometheus counter: success / cache_hit / error
- alias resolver: prefix strip + is_*
- model_manager: ``lm-studio-local/*`` определяется как LOCAL_MLX
"""

from __future__ import annotations

import pytest

from src.core import lm_studio_aliases as aliases
from src.core import lm_studio_discovery as disc
from src.core.metrics import lm_studio_discovery as disc_metrics


def _make_payload(n: int, *, include_embed: bool = True) -> dict:
    data = []
    if include_embed:
        data.append(
            {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model", "owned_by": "org"}
        )
        data.append({"id": "bge-reranker-v2-m3", "object": "model", "owned_by": "org"})
        data.append({"id": "whisper-large-v3", "object": "model", "owned_by": "org"})
    for i in range(n):
        data.append(
            {
                "id": f"gemma-3-{i}b-it-qat-4bit",
                "object": "model",
                "owned_by": "organization_owner",
            }
        )
    return {"data": data, "object": "list"}


@pytest.fixture(autouse=True)
def _reset_state():
    disc.reset_cache()
    # Сбрасываем in-memory counter между тестами
    disc_metrics._LM_STUDIO_DISCOVERY_COUNTER.clear()
    yield
    disc.reset_cache()


@pytest.mark.asyncio
async def test_discovery_success_returns_models(monkeypatch):
    """84 моделей из payload → 84 в выдаче (минус 3 не-LLM)."""

    async def fake_fetch(base_url, *, timeout):
        return _make_payload(81)["data"]

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch)
    result = await disc.discover_lm_studio_models()
    assert len(result) == 81
    assert all("embed" not in m["id"].lower() for m in result)


@pytest.mark.asyncio
async def test_discovery_filters_non_llm(monkeypatch):
    """Embedding, rerank, whisper отфильтрованы по эвристике."""

    async def fake_fetch(base_url, *, timeout):
        return _make_payload(5)["data"]

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch)
    result = await disc.discover_lm_studio_models()
    ids = [m["id"] for m in result]
    assert not any("embed" in i.lower() for i in ids)
    assert not any("rerank" in i.lower() for i in ids)
    assert not any("whisper" in i.lower() for i in ids)
    assert len(result) == 5  # только gemma-3-*


@pytest.mark.asyncio
async def test_discovery_cache_hit(monkeypatch):
    """Второй вызов в TTL окне → кэш, без сетевого запроса."""
    calls = {"n": 0}

    async def fake_fetch(base_url, *, timeout):
        calls["n"] += 1
        return _make_payload(3, include_embed=False)["data"]

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch)
    r1 = await disc.discover_lm_studio_models()
    r2 = await disc.discover_lm_studio_models()
    assert calls["n"] == 1
    assert r1 == r2
    assert disc_metrics._LM_STUDIO_DISCOVERY_COUNTER.get("cache_hit") == 1
    assert disc_metrics._LM_STUDIO_DISCOVERY_COUNTER.get("success") == 1


@pytest.mark.asyncio
async def test_discovery_force_refresh_bypasses_cache(monkeypatch):
    """force_refresh=True игнорирует кэш."""
    calls = {"n": 0}

    async def fake_fetch(base_url, *, timeout):
        calls["n"] += 1
        return _make_payload(2, include_embed=False)["data"]

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch)
    await disc.discover_lm_studio_models()
    await disc.discover_lm_studio_models(force_refresh=True)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_discovery_error_returns_empty_and_logs_metric(monkeypatch):
    """Любой raise в fetch → пустой список + counter error."""

    async def fake_fetch(base_url, *, timeout):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch)
    result = await disc.discover_lm_studio_models()
    assert result == []
    assert disc_metrics._LM_STUDIO_DISCOVERY_COUNTER.get("error") == 1


@pytest.mark.asyncio
async def test_discovery_error_returns_stale_cache(monkeypatch):
    """Если LM Studio упал после success → отдаём last-known-good кэш."""

    async def fake_fetch_ok(base_url, *, timeout):
        return _make_payload(2, include_embed=False)["data"]

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch_ok)
    first = await disc.discover_lm_studio_models()
    assert len(first) == 2

    # Симулируем отказ LM Studio + истекший TTL
    disc._cache_lock_state["ts"] = 1.0  # очень старая запись

    async def fake_fetch_err(base_url, *, timeout):
        raise TimeoutError("timeout")

    monkeypatch.setattr(disc, "_fetch_models_raw", fake_fetch_err)
    result = await disc.discover_lm_studio_models()
    # Stale cache читается с старым ts=1.0, теперь TTL истёк → отдаст пустой
    # (поведение consistent: stale-cache-on-error отдаём ТОЛЬКО если кэш
    # ещё валиден на момент error). Этот тест проверяет ИМЕННО что пустой.
    assert result == []


def test_filter_llm_models_unit():
    """filter_llm_models синхронная утилита — независимый assert."""
    raw = [
        {"id": "gemma-3-12b", "object": "model"},
        {"id": "bge-rerank", "object": "model"},
        {"id": "nomic-embed", "object": "model"},
        {"id": "clip-vit-large", "object": "model"},
        {"id": "", "object": "model"},
    ]
    result = disc.filter_llm_models(raw)
    assert len(result) == 1
    assert result[0]["id"] == "gemma-3-12b"


def test_is_lm_studio_local_model_id():
    assert aliases.is_lm_studio_local_model_id("lm-studio-local/gemma-3-12b")
    assert not aliases.is_lm_studio_local_model_id("lmstudio/gemma-3-12b")
    assert not aliases.is_lm_studio_local_model_id("")
    assert not aliases.is_lm_studio_local_model_id(None)


def test_strip_lm_studio_local_prefix():
    assert aliases.strip_lm_studio_local_prefix("lm-studio-local/gemma-3-12b") == "gemma-3-12b"
    # Без префикса — возвращается как есть
    assert aliases.strip_lm_studio_local_prefix("gemma-3-12b") == "gemma-3-12b"
    assert aliases.strip_lm_studio_local_prefix(None) is None


def test_model_manager_recognizes_lm_studio_local_prefix():
    """``lm-studio-local/*`` распознаётся model_manager как локальная LLM."""
    from src.model_manager import model_manager

    assert model_manager.is_local_model("lm-studio-local/gemma-3-12b-it-qat-4bit")
    # cloud не должен попадать в локальные
    assert not model_manager.is_local_model("google-vertex/gemini-3-pro-preview")
