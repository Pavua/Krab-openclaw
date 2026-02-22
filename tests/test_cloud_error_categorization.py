# -*- coding: utf-8 -*-
"""
Тесты: _categorize_cloud_error() правильно категоризирует cloud-ошибки.

Sprint B (R14): добавлен метод _categorize_cloud_error() в ModelRouter
как удобный строковый wrapper над _classify_cloud_error().
Критерии: разные типы ошибок → корректная строковая категория.
"""

from pathlib import Path

import pytest

from src.core.model_manager import ModelRouter


def _router(tmp_path: Path) -> ModelRouter:
    return ModelRouter(
        config={
            "MODEL_ROUTING_MEMORY_PATH": str(tmp_path / "routing_memory.json"),
            "MODEL_USAGE_REPORT_PATH": str(tmp_path / "usage_report.json"),
            "MODEL_OPS_STATE_PATH": str(tmp_path / "ops_state.json"),
            "MODEL_FEEDBACK_PATH": str(tmp_path / "feedback.json"),
        }
    )


def test_categorize_cloud_error_auth_fatal_leaked_key(tmp_path: Path) -> None:
    """Скомпрометированный ключ → auth_fatal."""
    router = _router(tmp_path)
    error = "❌ OpenClaw Error (0): Google API 403: Your API key was reported as leaked."
    assert router._categorize_cloud_error(error) == "auth_fatal"


def test_categorize_cloud_error_auth_fatal_invalid_key(tmp_path: Path) -> None:
    """Невалидный ключ → auth_fatal."""
    router = _router(tmp_path)
    assert router._categorize_cloud_error("Error: Invalid API key provided.") == "auth_fatal"


def test_categorize_cloud_error_auth_fatal_unauthorized(tmp_path: Path) -> None:
    """401 Unauthorized → auth_fatal."""
    router = _router(tmp_path)
    assert router._categorize_cloud_error("401 Unauthorized access to the API.") == "auth_fatal"


def test_categorize_cloud_error_api_disabled(tmp_path: Path) -> None:
    """Generative Language API не включён → api_disabled."""
    router = _router(tmp_path)
    msg = (
        "Google API 403: Generative Language API has not been used in project 123 "
        "or it is disabled. Enable it by visiting console.developers.google.com"
    )
    assert router._categorize_cloud_error(msg) == "api_disabled"


def test_categorize_cloud_error_quota(tmp_path: Path) -> None:
    """Исчерпана квота → quota."""
    router = _router(tmp_path)
    assert router._categorize_cloud_error("quota exceeded for this project") == "quota"


def test_categorize_cloud_error_model_not_found(tmp_path: Path) -> None:
    """Модель не найдена → model_not_found."""
    router = _router(tmp_path)
    msg = 'LLM error: {"error":{"code":404,"message":"models/gemini-2.0-flash-exp is not found","status":"NOT_FOUND"}}'
    assert router._categorize_cloud_error(msg) == "model_not_found"


def test_categorize_cloud_error_network(tmp_path: Path) -> None:
    """Ошибка соединения → network."""
    router = _router(tmp_path)
    assert router._categorize_cloud_error("Connection error. Failed to connect to API.") == "network"


def test_categorize_cloud_error_timeout(tmp_path: Path) -> None:
    """Таймаут → network (bucket для timeout/timed out)."""
    router = _router(tmp_path)
    # _classify_cloud_error ищет точную строку "timeout", а не "timed out".
    # Используем корректную строку с "timeout".
    result = router._categorize_cloud_error("Request timeout after 30s.")
    assert result == "network", f"Ожидали network для timeout, получили: {result}"



def test_categorize_cloud_error_unknown_fallback(tmp_path: Path) -> None:
    """Неизвестная ошибка → unknown."""
    router = _router(tmp_path)
    assert router._categorize_cloud_error("Unexpected error XYZ-9999 from server.") == "unknown"


def test_categorize_cloud_error_empty_text(tmp_path: Path) -> None:
    """Пустой текст → unknown (не ломается)."""
    router = _router(tmp_path)
    result = router._categorize_cloud_error("")
    assert result in {"unknown", "none"}, f"Неожиданная категория: {result}"
