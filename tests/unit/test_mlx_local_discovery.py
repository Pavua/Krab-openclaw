# -*- coding: utf-8 -*-
"""Wave 240: тесты для динамического discovery MLX local backend (:8088)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.core import mlx_local_discovery
from src.core.mlx_local_discovery import (
    _STATIC_FALLBACK_MODELS,
    _short_alias_for,
    build_mlx_local_provider_group,
    discover_mlx_local_models,
    get_discovery_cache_info,
    get_runtime_extended_alias_map,
)


@pytest.fixture(autouse=True)
def _reset_cache_and_persistence(tmp_path, monkeypatch):
    """Сбрасываем in-memory cache и redirect'им persistence в tmp_path."""
    mlx_local_discovery._reset_cache()
    # Redirect persisted cache в изолированный tmp dir.
    monkeypatch.setattr(
        mlx_local_discovery,
        "_persisted_cache_path",
        lambda: tmp_path / "mlx_local_aliases_runtime.json",
    )
    yield
    mlx_local_discovery._reset_cache()


def _mock_response(payload: dict, status_code: int = 200) -> MagicMock:
    """Собирает MagicMock который ведёт себя как httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _patch_http(payload: dict | None = None, *, exc: Exception | None = None):
    """Helper: patch httpx.Client.get/post."""
    if exc is not None:

        def _raise(*_a, **_k):
            raise exc

        return patch.object(mlx_local_discovery.httpx, "Client", side_effect=_raise)

    def _factory(*_a, **_k):
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.return_value = _mock_response(payload or {})
        return client

    factory_mock = MagicMock(side_effect=_factory)
    return patch.object(mlx_local_discovery.httpx, "Client", factory_mock)


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Discovery success — live :8088 ответил списком моделей
# ─────────────────────────────────────────────────────────────────────────────


def test_discovery_success_returns_live_models():
    """`/v1/models` отдал 2 модели → discovery возвращает их с alias'ами."""
    payload = {
        "object": "list",
        "data": [
            {
                "id": "/Volumes/4TB SSD/LMStudio_models/mlx-community/Gemma-4-26B-IT",
                "object": "model",
            },
            {
                "id": "/Volumes/4TB SSD/LMStudio_models/mlx-community/Qwen3-14B-Foo",
                "object": "model",
            },
        ],
    }
    with _patch_http(payload):
        models = discover_mlx_local_models(force_refresh=True)
    assert len(models) == 2
    ids = [m["id"] for m in models]
    assert "mlx-local-kv4/gemma-4-26b-it" in ids
    assert "mlx-local-kv4/qwen3-14b-foo" in ids
    # Full paths preserved.
    assert all(m["full_path"].startswith("/Volumes/") for m in models)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Short alias generation — lowercased + cleaned
# ─────────────────────────────────────────────────────────────────────────────


def test_short_alias_lowercased_and_cleaned():
    """Basename lowercased + alphanum/dash only."""
    short_id, short_base = _short_alias_for(
        "/Volumes/4TB SSD/LMStudio_models/mlx-community/Gemma-4-26B-A4B-it-OptiQ-4bit"
    )
    assert short_id == "mlx-local-kv4/gemma-4-26b-a4b-it-optiq-4bit"
    assert short_base == "gemma-4-26b-a4b-it-optiq-4bit"


def test_short_alias_empty_path_returns_empty():
    """Пустой путь → пустой alias (caller отфильтрует)."""
    assert _short_alias_for("") == ("", "")
    assert _short_alias_for("   ") == ("", "")


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Cache TTL — повторный вызов в окне 30s отдаёт cached
# ─────────────────────────────────────────────────────────────────────────────


def test_cache_hit_within_ttl_avoids_http():
    """В рамках 30s TTL discovery не делает повторный HTTP."""
    payload = {
        "object": "list",
        "data": [
            {"id": "/Volumes/.../foo", "object": "model"},
        ],
    }
    with _patch_http(payload) as mock_factory:
        discover_mlx_local_models(force_refresh=True)
        # Второй вызов — должен попасть в кэш.
        discover_mlx_local_models()
    # MagicMock side_effect (factory) вызывается на каждый httpx.Client() —
    # проверяем что был ровно один call.
    assert mock_factory.call_count == 1


def test_force_refresh_bypasses_cache():
    """force_refresh=True игнорирует in-memory кэш."""
    payload = {"object": "list", "data": [{"id": "/Volumes/.../bar", "object": "model"}]}
    with _patch_http(payload) as mock_factory:
        discover_mlx_local_models(force_refresh=True)
        discover_mlx_local_models(force_refresh=True)
    assert mock_factory.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Fallback на static при HTTP-ошибке
# ─────────────────────────────────────────────────────────────────────────────


def test_fallback_to_static_on_connect_error():
    """При connect-fail и пустом persisted cache → возвращаем Wave 182 static."""
    with _patch_http(exc=httpx.ConnectError("nope")):
        models = discover_mlx_local_models(force_refresh=True)
    static_ids = {m["id"] for m in _STATIC_FALLBACK_MODELS}
    returned_ids = {m["id"] for m in models}
    assert returned_ids == static_ids


def test_fallback_to_persisted_when_recent(tmp_path, monkeypatch):
    """Если persisted JSON свежее 24h — используем его до static."""
    persisted_path = tmp_path / "mlx_local_aliases_runtime.json"
    monkeypatch.setattr(mlx_local_discovery, "_persisted_cache_path", lambda: persisted_path)
    persisted_path.write_text(
        json.dumps(
            {
                "ts": time.time(),
                "aliases": {"mlx-local-kv4/persisted-model": "/Volumes/persisted/path"},
            }
        )
    )
    with _patch_http(exc=httpx.ConnectError("nope")):
        models = discover_mlx_local_models(force_refresh=True)
    ids = [m["id"] for m in models]
    assert "mlx-local-kv4/persisted-model" in ids


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Persist runtime aliases survives restart
# ─────────────────────────────────────────────────────────────────────────────


def test_successful_discovery_writes_persisted_cache(tmp_path, monkeypatch):
    """Успешный discovery пишет в `mlx_local_aliases_runtime.json`."""
    persisted_path = tmp_path / "mlx_local_aliases_runtime.json"
    monkeypatch.setattr(mlx_local_discovery, "_persisted_cache_path", lambda: persisted_path)
    payload = {
        "object": "list",
        "data": [
            {"id": "/Volumes/.../baz-model", "object": "model"},
        ],
    }
    with _patch_http(payload):
        discover_mlx_local_models(force_refresh=True)
    assert persisted_path.exists()
    raw = json.loads(persisted_path.read_text())
    assert "aliases" in raw
    assert any("baz-model" in v for v in raw["aliases"].values())


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: build_mlx_local_provider_group — UI shape
# ─────────────────────────────────────────────────────────────────────────────


def test_provider_group_keeps_static_id_and_label():
    """Wave 182 contract: id=mlx-local-kv4, label="MLX KV4 (Local :8088)"."""
    with _patch_http(exc=httpx.ConnectError("offline")):
        group = build_mlx_local_provider_group(force_refresh=True)
    assert group["id"] == "mlx-local-kv4"
    assert group["label"] == "MLX KV4 (Local :8088)"
    assert group["type"] == "local"
    # models — list of (id, label) tuples (совместимо с pre-Wave-224 picker).
    assert isinstance(group["models"], list)
    assert all(isinstance(t, tuple) and len(t) == 2 for t in group["models"])


def test_provider_group_includes_live_models_when_discovered():
    """Live discovery → group содержит discovered модели."""
    payload = {
        "object": "list",
        "data": [
            {"id": "/Volumes/.../Llama-Quant-X", "object": "model"},
        ],
    }
    with _patch_http(payload):
        group = build_mlx_local_provider_group(force_refresh=True)
    ids = [t[0] for t in group["models"]]
    assert "mlx-local-kv4/llama-quant-x" in ids
    assert group["discovery_source"] == "live"


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: RotorQuant compatibility — model swap reflected after TTL
# ─────────────────────────────────────────────────────────────────────────────


def test_rotorquant_swap_reflected_after_cache_expiry(monkeypatch):
    """RotorQuant swap'нул модель → после истечения TTL мы видим новую."""
    # Первая discovery — Gemma loaded.
    payload_a = {
        "object": "list",
        "data": [{"id": "/Volumes/.../gemma-baseline", "object": "model"}],
    }
    with _patch_http(payload_a):
        first = discover_mlx_local_models(force_refresh=True)
    assert any("gemma-baseline" in m["id"] for m in first)

    # Эмулируем истёкший cache (TTL прошёл).
    mlx_local_discovery._CACHE["ts"] = 0.0

    # RotorQuant swap'нул на draft модель для speculative decoding.
    payload_b = {
        "object": "list",
        "data": [{"id": "/Volumes/.../qwen3-draft-mlx", "object": "model"}],
    }
    with _patch_http(payload_b):
        second = discover_mlx_local_models()
    ids = [m["id"] for m in second]
    assert "mlx-local-kv4/qwen3-draft-mlx" in ids
    assert "mlx-local-kv4/gemma-baseline" not in ids


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Runtime extended alias map merges discovery
# ─────────────────────────────────────────────────────────────────────────────


def test_runtime_extended_alias_map_merges_discovered():
    """get_runtime_extended_alias_map() добавляет discovered поверх defaults."""
    payload = {
        "object": "list",
        "data": [
            {"id": "/Volumes/.../discovered-only-model", "object": "model"},
        ],
    }
    with _patch_http(payload):
        merged = get_runtime_extended_alias_map()
    # Default Wave 222 alias всё ещё там.
    assert "mlx-local-kv4/gemma-4-26b" in merged
    # Discovered тоже.
    assert "mlx-local-kv4/discovered-only-model" in merged
    assert merged["mlx-local-kv4/discovered-only-model"].endswith("discovered-only-model")


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Cache info diagnostic
# ─────────────────────────────────────────────────────────────────────────────


def test_cache_info_reports_state():
    """get_discovery_cache_info() возвращает текущий state."""
    payload = {
        "object": "list",
        "data": [{"id": "/Volumes/.../m1", "object": "model"}],
    }
    with _patch_http(payload):
        discover_mlx_local_models(force_refresh=True)
    info = get_discovery_cache_info()
    assert info["fresh"] is True
    assert info["source"] == "live"
    assert info["count"] >= 1
    assert info["ttl_sec"] == 30.0
    assert "backend_url" in info


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: Prometheus counter inc-ит на success/error/fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_prometheus_counter_increments_on_success():
    """krab_mlx_local_discovery_total{result=success} +1 на live success."""
    counter = mlx_local_discovery.krab_mlx_local_discovery_total
    if counter is None:
        pytest.skip("prometheus_client not installed")
    before = counter.labels(result="success")._value.get()
    payload = {
        "object": "list",
        "data": [{"id": "/Volumes/.../foo", "object": "model"}],
    }
    with _patch_http(payload):
        discover_mlx_local_models(force_refresh=True)
    after = counter.labels(result="success")._value.get()
    assert after == before + 1


def test_prometheus_counter_increments_on_fallback():
    """krab_mlx_local_discovery_total{result=fallback} на static fallback."""
    counter = mlx_local_discovery.krab_mlx_local_discovery_total
    if counter is None:
        pytest.skip("prometheus_client not installed")
    before_fallback = counter.labels(result="fallback")._value.get()
    before_error = counter.labels(result="error")._value.get()
    with _patch_http(exc=httpx.ConnectError("offline")):
        discover_mlx_local_models(force_refresh=True)
    # И error и fallback должны инкрементнуться (error при failed request,
    # fallback при возврате static).
    assert counter.labels(result="error")._value.get() == before_error + 1
    assert counter.labels(result="fallback")._value.get() == before_fallback + 1
