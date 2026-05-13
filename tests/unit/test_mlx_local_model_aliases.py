# -*- coding: utf-8 -*-
"""Wave 222: тесты для alias-слоя MLX local backend."""

from __future__ import annotations

import json

import pytest

from src.core.mlx_local_aliases import (
    get_alias_map,
    is_mlx_local_target,
    resolve_mlx_local_alias,
    reverse_mlx_local_alias,
)


def test_alias_hit_resolves_short_to_full_path() -> None:
    """Короткий id `mlx-local-kv4/gemma-4-26b` → полный путь."""
    full = resolve_mlx_local_alias(
        "mlx-local-kv4/gemma-4-26b",
        target_url="http://127.0.0.1:8088",
    )
    assert full is not None
    assert full.endswith("gemma-4-26B-A4B-it-OptiQ-4bit")
    assert full.startswith("/Volumes/")


def test_alias_miss_passes_through_unchanged() -> None:
    """Незнакомое имя модели возвращается as-is для MLX local URL."""
    result = resolve_mlx_local_alias(
        "google/gemini-3-pro-preview",
        target_url="http://127.0.0.1:8088",
    )
    assert result == "google/gemini-3-pro-preview"


def test_non_mlx_target_passes_through_even_known_alias() -> None:
    """Для НЕ-MLX backend (например LM Studio :1234) подстановка не делается.

    Это критично: если RotorQuant решит послать `mlx-local-kv4/...` в обычный
    LM Studio :1234 — мы НЕ должны мутировать имя.
    """
    result = resolve_mlx_local_alias(
        "mlx-local-kv4/gemma-4-26b",
        target_url="http://192.168.0.171:1234",
    )
    assert result == "mlx-local-kv4/gemma-4-26b"


def test_env_override_extends_alias_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENV `MLX_LOCAL_MODEL_ALIASES_JSON` добавляет новые маппинги без code-changes."""
    extra = {
        "mlx-local-kv4/my-custom": "/Volumes/4TB SSD/LMStudio_models/custom/path",
    }
    monkeypatch.setenv("MLX_LOCAL_MODEL_ALIASES_JSON", json.dumps(extra))
    full = resolve_mlx_local_alias(
        "mlx-local-kv4/my-custom",
        target_url="http://127.0.0.1:8088",
    )
    assert full == "/Volumes/4TB SSD/LMStudio_models/custom/path"
    # И get_alias_map() возвращает merged dict
    merged = get_alias_map()
    assert merged["mlx-local-kv4/my-custom"] == "/Volumes/4TB SSD/LMStudio_models/custom/path"
    # Defaults остались
    assert "mlx-local-kv4/gemma-4-26b" in merged


def test_env_override_invalid_json_falls_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Битый JSON в ENV не должен ронять resolve — только defaults остаются."""
    monkeypatch.setenv("MLX_LOCAL_MODEL_ALIASES_JSON", "{not valid json")
    full = resolve_mlx_local_alias(
        "mlx-local-kv4/gemma-4-26b",
        target_url="http://127.0.0.1:8088",
    )
    assert full is not None and full.endswith("gemma-4-26B-A4B-it-OptiQ-4bit")


def test_reverse_alias_for_logs() -> None:
    """Полный путь → короткое имя, для чистых логов."""
    full = "/Volumes/4TB SSD/LMStudio_models/mlx-community/gemma-4-26B-A4B-it-OptiQ-4bit"
    assert reverse_mlx_local_alias(full) == "mlx-local-kv4/gemma-4-26b"


def test_reverse_alias_unknown_path_passes_through() -> None:
    """Неизвестный полный путь — возвращаем as-is."""
    unknown = "/some/random/path"
    assert reverse_mlx_local_alias(unknown) == unknown


def test_is_mlx_local_target_port_detection() -> None:
    """Детекция MLX local backend по порту (default :8088)."""
    assert is_mlx_local_target("http://127.0.0.1:8088")
    assert is_mlx_local_target("http://localhost:8088/v1/chat")
    assert not is_mlx_local_target("http://192.168.0.171:1234")
    assert not is_mlx_local_target("https://api.openai.com")
    assert not is_mlx_local_target("")
    assert not is_mlx_local_target(None)


def test_resolve_force_bypasses_target_check() -> None:
    """`force=True` — резолвим без проверки target_url (для unit-сценариев)."""
    full = resolve_mlx_local_alias(
        "mlx-local-kv4/gemma-4-26b",
        target_url=None,
        force=True,
    )
    assert full is not None and full.endswith("gemma-4-26B-A4B-it-OptiQ-4bit")


def test_none_input_returns_none() -> None:
    """`None` на вход → `None` на выход (защита от NPE)."""
    assert resolve_mlx_local_alias(None, target_url="http://127.0.0.1:8088") is None
