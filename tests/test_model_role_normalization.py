# -*- coding: utf-8 -*-
"""Тесты нормализации ролей контекста для LM Studio/OpenAI-совместимых API."""

from src.core.model_manager import ModelRouter


def test_normalize_chat_role_known_roles():
    assert ModelRouter._normalize_chat_role("user") == "user"
    assert ModelRouter._normalize_chat_role("assistant") == "assistant"
    assert ModelRouter._normalize_chat_role("system") == "system"
    assert ModelRouter._normalize_chat_role("tool") == "tool"


def test_normalize_chat_role_legacy_aliases():
    assert ModelRouter._normalize_chat_role("model") == "assistant"
    assert ModelRouter._normalize_chat_role("vision_analysis") == "assistant"
    assert ModelRouter._normalize_chat_role("analysis") == "system"


def test_normalize_chat_role_fallback():
    assert ModelRouter._normalize_chat_role("unknown_custom_role") == "user"
    assert ModelRouter._normalize_chat_role("") == "user"
