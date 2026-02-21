# -*- coding: utf-8 -*-
"""Тесты контракта парсинга команды !model set и алиасов моделей."""

from src.handlers.commands import (
    parse_model_set_request,
    resolve_local_model_size_human,
    normalize_model_alias,
)


def test_parse_model_set_canonical_format() -> None:
    parsed = parse_model_set_request(
        ["model", "set", "chat", "zai-org/glm-4.6v-flash"],
        ["chat", "thinking", "pro", "coding"],
    )
    assert parsed["ok"] is True
    assert parsed["slot"] == "chat"
    assert parsed["model_name"] == "zai-org/glm-4.6v-flash"
    assert parsed["legacy"] is False


def test_parse_model_set_legacy_format_maps_to_chat() -> None:
    parsed = parse_model_set_request(
        ["model", "set", "zai-org/glm-4.6v-flash"],
        ["chat", "thinking", "pro", "coding"],
    )
    assert parsed["ok"] is True
    assert parsed["slot"] == "chat"
    assert parsed["model_name"] == "zai-org/glm-4.6v-flash"
    assert parsed["legacy"] is True
    assert "Legacy-формат" in str(parsed["warning"])


def test_parse_model_set_rejects_unknown_slot() -> None:
    parsed = parse_model_set_request(
        ["model", "set", "moderation", "zai-org/glm-4.6v-flash"],
        ["chat", "thinking", "pro", "coding"],
    )
    assert parsed["ok"] is False
    assert "Неизвестный слот" in str(parsed["error"])


def test_parse_model_set_requires_model_after_slot() -> None:
    parsed = parse_model_set_request(
        ["model", "set", "chat"],
        ["chat", "thinking", "pro", "coding"],
    )
    assert parsed["ok"] is False
    assert "Формат команды" in str(parsed["error"])


def test_resolve_local_model_size_uses_verbose_map_first() -> None:
    class _Router:
        def _estimate_model_size_gb(self, _model_id: str) -> float:
            return 99.0

    size = resolve_local_model_size_human(
        _Router(),
        "zai-org/glm-4.6v-flash",
        {"zai-org/glm-4.6v-flash": {"size_human": "7.1 GB"}},
    )
    assert size == "7.1 GB"


def test_resolve_local_model_size_falls_back_to_estimate() -> None:
    class _Router:
        def _estimate_model_size_gb(self, _model_id: str) -> float:
            return 6.8

    size = resolve_local_model_size_human(_Router(), "qwen/local", {})
    assert size == "6.8 GB"


def test_normalize_model_alias_gemini_shortcut() -> None:
    normalized, note = normalize_model_alias("gemini-3-pro-latest")
    assert normalized == "google/gemini-3-pro-preview"
    assert "Алиас" in note


def test_normalize_model_alias_openai_shortcut() -> None:
    normalized, note = normalize_model_alias("gpt-5-mini")
    assert normalized == "openai/gpt-5-mini"
    assert "Алиас" in note


def test_normalize_model_alias_keeps_full_id() -> None:
    model_id = "zai-org/glm-4.6v-flash"
    normalized, note = normalize_model_alias(model_id)
    assert normalized == model_id
    assert note == ""
