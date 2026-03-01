# -*- coding: utf-8 -*-
"""Тесты пробника cloud-ключей Gemini."""

from src.core.cloud_key_probe import (
    _extract_generate_models,
    _pick_probe_model,
    classify_gemini_http_error,
)


def test_extract_generate_models_filters_only_generate_content() -> None:
    payload = {
        "models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]
    }
    models = _extract_generate_models(payload)
    assert models == {"gemini-2.5-flash"}


def test_pick_probe_model_uses_available_preferred_chain() -> None:
    available = {"gemini-2.5-pro", "gemini-1.5-flash"}
    chosen = _pick_probe_model("gemini-2.0-flash", available)
    assert chosen == "gemini-2.5-pro"


def test_classify_404_model_not_available() -> None:
    status, code, action = classify_gemini_http_error(
        404,
        "This model is no longer available to new users.",
    )
    assert status == "error"
    assert code == "model_not_available"
    assert action == "switch_model"
