# -*- coding: utf-8 -*-
"""
Тесты user-facing нормализации runtime-ошибок в AI-хендлере.
"""

from src.handlers.ai import _normalize_runtime_error_message_for_user


class _RouterStub:
    """Мини-стаб роутера для тестирования runtime-детектора."""

    def __init__(self, is_error: bool):
        self._is_error = bool(is_error)

    def _is_runtime_error_message(self, text: str) -> bool:
        return self._is_error


def test_runtime_error_guard_rewrites_connection_error() -> None:
    text, rewritten = _normalize_runtime_error_message_for_user("Connection error.")
    assert rewritten is True
    assert "Временная ошибка AI" in text
    assert "соединения" in text


def test_runtime_error_guard_rewrites_no_models_loaded() -> None:
    raw = "400 No models loaded. Please load a model in the developer page."
    text, rewritten = _normalize_runtime_error_message_for_user(raw)
    assert rewritten is True
    assert "локальная модель не загружена" in text


def test_runtime_error_guard_keeps_normal_text() -> None:
    raw = "Привет! Всё работает, вот ответ по задаче."
    text, rewritten = _normalize_runtime_error_message_for_user(raw)
    assert rewritten is False
    assert text == raw


def test_runtime_error_guard_respects_router_detector() -> None:
    router = _RouterStub(is_error=True)
    text, rewritten = _normalize_runtime_error_message_for_user("какой-то ответ без маркеров", router=router)
    assert rewritten is True
    assert "Временная ошибка AI" in text
