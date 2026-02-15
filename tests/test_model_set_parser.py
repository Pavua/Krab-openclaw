# -*- coding: utf-8 -*-
"""Тесты контракта парсинга команды !model set."""

from src.handlers.commands import parse_model_set_request


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
