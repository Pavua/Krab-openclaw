# -*- coding: utf-8 -*-
"""
Тесты для алиасов облачных моделей.

Зачем нужны:
- фиксируют актуальный канонический id для "умного" Gemini-профиля;
- страхуют web/UI и командные алиасы от отката на устаревший preview-id.
"""

from __future__ import annotations

from src.core.model_aliases import normalize_model_alias, render_model_presets_text


def test_normalize_model_alias_maps_gemini_3_pro_alias_to_current_preview() -> None:
    resolved, info = normalize_model_alias("gemini-3-pro-latest")
    assert resolved == "google/gemini-3.1-pro-preview"
    assert "gemini-3.1-pro-preview" in info


def test_render_model_presets_text_mentions_current_gemini_pro_preview() -> None:
    text = render_model_presets_text()
    assert "google/gemini-3.1-pro-preview" in text
