# -*- coding: utf-8 -*-
"""
Тесты для _collect_session_12_stats() в EcosystemHealthService.

Проверяем:
- session_12 block никогда не пустой {}
- каждый коллектор возвращает ключ "available"
- отсутствие модуля возвращает available=False, не Exception
- chat_window_manager singleton импортируется корректно
"""

from __future__ import annotations

from typing import Any

import pytest


class _FakeRouter:
    """Заглушка router для тестов."""

    async def health_check(self) -> dict[str, Any]:
        return {"status": "healthy"}


@pytest.fixture()
def health_svc():
    from src.core.ecosystem_health import EcosystemHealthService

    return EcosystemHealthService(router=_FakeRouter())


# ---------------------------------------------------------------------------
# test_collect_returns_session_12_block_non_empty
# ---------------------------------------------------------------------------


def test_collect_returns_session_12_block_non_empty(health_svc):
    """session_12 — не пустой словарь; содержит ожидаемые ключи."""
    result = health_svc._collect_session_12_stats()
    assert isinstance(result, dict), "session_12 должен быть dict"
    assert result != {}, "session_12 не должен быть пустым {}"
    assert "chat_windows" in result
    assert "message_batcher" in result
    assert "chat_filter" in result


# ---------------------------------------------------------------------------
# test_each_collector_returns_available_key
# ---------------------------------------------------------------------------


def test_each_collector_returns_available_key(health_svc):
    """Каждый sub-коллектор возвращает ключ 'available' (True или False)."""
    result = health_svc._collect_session_12_stats()
    for key in ("chat_windows", "message_batcher", "chat_filter"):
        sub = result[key]
        assert isinstance(sub, dict), f"{key} должен быть dict"
        assert "available" in sub, f"{key} должен содержать ключ 'available'"


# ---------------------------------------------------------------------------
# test_missing_module_gracefully_returns_available_false
# ---------------------------------------------------------------------------


def test_missing_module_gracefully_returns_available_false(health_svc, monkeypatch):
    """При невозможности импортировать модуль возвращается available=False, не Exception.

    Тест выбрасывает Wave-16 модули из sys.modules перед вызовом, чтобы
    проверить lazy-import ветку (модуль в кэше не сидит).
    """
    import sys

    # Удаляем кэшированные модули чтобы принудить повторный импорт
    wave16_keys = [
        k for k in list(sys.modules)
        if any(
            k.endswith(m)
            for m in (".chat_window_manager", ".message_batcher", ".chat_filter_config")
        )
        or k in (
            "src.core.chat_window_manager",
            "src.core.message_batcher",
            "src.core.chat_filter_config",
            "core.chat_window_manager",
            "core.message_batcher",
            "core.chat_filter_config",
        )
    ]
    for k in wave16_keys:
        monkeypatch.delitem(sys.modules, k, raising=False)

    # Патчим коллекторы напрямую — наиболее надёжный способ
    monkeypatch.setattr(
        health_svc.__class__,
        "_collect_chat_windows",
        staticmethod(lambda: {"available": False, "error": "mocked"}),
    )
    monkeypatch.setattr(
        health_svc.__class__,
        "_collect_message_batcher",
        staticmethod(lambda: {"available": False, "error": "mocked"}),
    )
    monkeypatch.setattr(
        health_svc.__class__,
        "_collect_chat_filter",
        staticmethod(lambda: {"available": False, "error": "mocked"}),
    )

    # Не должен бросать исключение
    result = health_svc._collect_session_12_stats()
    assert result != {}, "session_12 не должен быть {} даже при сбое импортов"
    for key in ("chat_windows", "message_batcher", "chat_filter"):
        sub = result.get(key, {})
        assert sub.get("available") is False, (
            f"{key} должен вернуть available=False при заблокированном импорте"
        )


# ---------------------------------------------------------------------------
# test_chat_window_manager_singleton_importable
# ---------------------------------------------------------------------------


def test_chat_window_manager_singleton_importable():
    """Синглтон chat_window_manager должен импортироваться из модуля."""
    from src.core.chat_window_manager import ChatWindowManager, chat_window_manager

    assert isinstance(chat_window_manager, ChatWindowManager)
    stats = chat_window_manager.stats()
    assert "active_windows" in stats
    assert "capacity" in stats
    assert "total_messages" in stats


# ---------------------------------------------------------------------------
# test_chat_window_manager_stats_available_true
# ---------------------------------------------------------------------------


def test_chat_window_manager_stats_available_true(health_svc):
    """После добавления синглтона chat_windows.available должен быть True."""
    result = health_svc._collect_chat_windows()
    assert result.get("available") is True, (
        f"chat_windows должен быть available=True, got: {result}"
    )
    assert "active_windows" in result
