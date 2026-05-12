# -*- coding: utf-8 -*-
"""Wave 133: тесты LM Studio registry probe."""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest

from src.core.lm_studio_registry_probe import (
    LmStudioRegistryProbe,
    _estimate_model_ram_gb,
    _get_base_url,
    _get_interval_sec,
    _is_enabled,
    compute_registry_snapshot,
    parse_models_payload,
)


def test_parse_models_payload_openai_format():
    """Стандартный OpenAI-совместимый ответ."""
    payload = {
        "object": "list",
        "data": [
            {"id": "google/gemma-3-4b-it", "object": "model"},
            {"id": "qwen-2.5-7b-instruct", "object": "model"},
        ],
    }
    models = parse_models_payload(payload)
    assert len(models) == 2
    assert models[0]["id"] == "google/gemma-3-4b-it"


def test_parse_models_payload_malformed():
    """Защита от мусорных payload'ов."""
    assert parse_models_payload(None) == []
    assert parse_models_payload([]) == []
    assert parse_models_payload({"data": "not-a-list"}) == []
    assert parse_models_payload({"data": [42, {"id": "ok"}, "bad"]}) == [{"id": "ok"}]


def test_estimate_ram_by_name_billions_suffix():
    """Эвристика size-by-name: 4b → ~4 ГБ, 70b → ~40 ГБ."""
    assert _estimate_model_ram_gb({"id": "gemma-3-4b-it"}) == pytest.approx(4.0)
    assert _estimate_model_ram_gb({"id": "llama-3.3-70b"}) == pytest.approx(40.0)
    assert _estimate_model_ram_gb({"id": "qwen-2.5-7b-instruct"}) == pytest.approx(5.0)


def test_estimate_ram_uses_explicit_size_bytes():
    """Если API отдаёт size_bytes — используем его, конвертируя в ГБ."""
    one_gb_bytes = 1024**3 * 8  # 8 ГБ в байтах
    gb = _estimate_model_ram_gb({"id": "weird-name", "size_bytes": one_gb_bytes})
    assert gb == pytest.approx(8.0, rel=0.01)


def test_estimate_ram_default_for_unknown_name():
    """Unknown модель → консервативный default 4.0 ГБ."""
    assert _estimate_model_ram_gb({"id": "mystery-model"}) == pytest.approx(4.0)
    # Пустой payload
    assert _estimate_model_ram_gb({}) == pytest.approx(4.0)


def test_compute_registry_snapshot_aggregates():
    """count + сумма ГБ по списку моделей."""
    models = [
        {"id": "gemma-3-4b-it"},   # 4
        {"id": "qwen-2.5-7b"},     # 5
        {"id": "llama-3-8b"},      # 8
    ]
    count, total_gb = compute_registry_snapshot(models)
    assert count == 3
    assert total_gb == pytest.approx(17.0)


def test_env_gate_enabled_default_on():
    """KRAB_LM_REGISTRY_PROBE_ENABLED отсутствует → enabled."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("KRAB_LM_REGISTRY_PROBE_ENABLED", None)
        assert _is_enabled() is True
    with mock.patch.dict(os.environ, {"KRAB_LM_REGISTRY_PROBE_ENABLED": "0"}):
        assert _is_enabled() is False
    with mock.patch.dict(os.environ, {"KRAB_LM_REGISTRY_PROBE_ENABLED": "no"}):
        assert _is_enabled() is False


def test_env_interval_and_url_overrides():
    """Env overrides для interval/URL и защита от мусорных значений."""
    with mock.patch.dict(os.environ, {"KRAB_LM_REGISTRY_PROBE_INTERVAL_SEC": "120"}):
        assert _get_interval_sec() == 120
    with mock.patch.dict(os.environ, {"KRAB_LM_REGISTRY_PROBE_INTERVAL_SEC": "junk"}):
        assert _get_interval_sec() == 60
    # min clamp = 5
    with mock.patch.dict(os.environ, {"KRAB_LM_REGISTRY_PROBE_INTERVAL_SEC": "1"}):
        assert _get_interval_sec() == 5

    with mock.patch.dict(os.environ, {"LM_STUDIO_URL": "http://example.com:9999/"}):
        assert _get_base_url() == "http://example.com:9999"


def test_probe_once_sets_gauges():
    """probe_once вызывает set_lm_registry_state с правильными значениями."""
    fake_models = [{"id": "gemma-3-4b"}, {"id": "llama-3-8b"}]

    async def _fake_fetch(_url: str):
        return fake_models

    probe = LmStudioRegistryProbe(
        fetch_fn=_fake_fetch,
        interval_fn=lambda: 60,
        url_fn=lambda: "http://test:1234",
    )

    with mock.patch(
        "src.core.lm_studio_registry_probe.set_lm_registry_state"
    ) as set_state:
        count, total_gb = asyncio.run(probe.probe_once())

    assert count == 2
    assert total_gb == pytest.approx(12.0)  # 4 + 8
    set_state.assert_called_once_with(loaded_count=2, estimated_ram_gb=12.0)


def test_probe_once_handles_empty_response():
    """Пустой/недоступный LM Studio → count=0, ram=0, gauges всё равно пишутся."""

    async def _fake_fetch(_url: str):
        return []

    probe = LmStudioRegistryProbe(fetch_fn=_fake_fetch)
    with mock.patch(
        "src.core.lm_studio_registry_probe.set_lm_registry_state"
    ) as set_state:
        count, total_gb = asyncio.run(probe.probe_once())

    assert count == 0
    assert total_gb == 0.0
    set_state.assert_called_once_with(loaded_count=0, estimated_ram_gb=0.0)
