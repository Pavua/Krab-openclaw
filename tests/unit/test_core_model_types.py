# -*- coding: utf-8 -*-
"""Unit-тесты для src/core/model_types.py: ModelStatus, ModelType, ModelInfo."""

from __future__ import annotations

from src.core.model_types import ModelInfo, ModelStatus, ModelType

# ---------------------------------------------------------------------------
# ModelStatus
# ---------------------------------------------------------------------------


def test_model_status_values() -> None:
    """Все ожидаемые строковые значения enum присутствуют."""
    assert ModelStatus.AVAILABLE.value == "available"
    assert ModelStatus.LOADED.value == "loaded"
    assert ModelStatus.LOADING.value == "loading"
    assert ModelStatus.UNLOADING.value == "unloading"
    assert ModelStatus.ERROR.value == "error"
    assert ModelStatus.UNKNOWN.value == "unknown"


def test_model_status_count() -> None:
    """Количество значений enum не изменилось неявно."""
    assert len(ModelStatus) == 6


def test_model_status_lookup_by_value() -> None:
    """Можно восстановить enum по строковому значению."""
    assert ModelStatus("loaded") is ModelStatus.LOADED
    assert ModelStatus("error") is ModelStatus.ERROR


# ---------------------------------------------------------------------------
# ModelType
# ---------------------------------------------------------------------------


def test_model_type_values() -> None:
    """Строковые значения enum совпадают со спецификацией."""
    assert ModelType.LOCAL_MLX.value == "mlx"
    assert ModelType.LOCAL_GGUF.value == "gguf"
    assert ModelType.CLI_BACKEND.value == "cli_backend"
    assert ModelType.CLOUD_GEMINI.value == "gemini"
    assert ModelType.CLOUD_OPENROUTER.value == "openrouter"


def test_model_type_count() -> None:
    """Количество типов не изменилось неявно."""
    assert len(ModelType) == 5


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------


def test_model_info_defaults() -> None:
    """Поля по умолчанию соответствуют документации."""
    info = ModelInfo(id="test-id", name="Test Model", type=ModelType.CLOUD_GEMINI)
    assert info.status is ModelStatus.UNKNOWN
    assert info.size_gb == 8.0
    assert info.context_window == 8192
    assert info.supports_vision is False


def test_model_info_explicit_fields() -> None:
    """Явно переданные значения сохраняются без изменений."""
    info = ModelInfo(
        id="mlx-123",
        name="MLX Model",
        type=ModelType.LOCAL_MLX,
        status=ModelStatus.LOADED,
        size_gb=4.5,
        context_window=32768,
        supports_vision=True,
    )
    assert info.id == "mlx-123"
    assert info.name == "MLX Model"
    assert info.status is ModelStatus.LOADED
    assert info.size_gb == 4.5
    assert info.context_window == 32768
    assert info.supports_vision is True


def test_model_info_is_local_true_for_mlx() -> None:
    """is_local возвращает True для LOCAL_MLX."""
    info = ModelInfo(id="x", name="x", type=ModelType.LOCAL_MLX)
    assert info.is_local is True


def test_model_info_is_local_true_for_gguf() -> None:
    """is_local возвращает True для LOCAL_GGUF."""
    info = ModelInfo(id="x", name="x", type=ModelType.LOCAL_GGUF)
    assert info.is_local is True


def test_model_info_is_local_false_for_cloud_types() -> None:
    """is_local возвращает False для облачных и cli-типов."""
    for cloud_type in (ModelType.CLOUD_GEMINI, ModelType.CLOUD_OPENROUTER, ModelType.CLI_BACKEND):
        info = ModelInfo(id="x", name="x", type=cloud_type)
        assert info.is_local is False, f"Ожидалось False для {cloud_type}"
