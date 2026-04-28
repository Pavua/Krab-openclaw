# -*- coding: utf-8 -*-
"""Тесты для LLMTextProcessingMixin._strip_phrase_parasites — Bug 9 (Session 28)."""
from __future__ import annotations

from src.userbot.llm_text_processing import LLMTextProcessingMixin


class _Host(LLMTextProcessingMixin):
    """Минимальный host для классовых методов mixin'а."""


def strip(text: str) -> str:
    return _Host._strip_phrase_parasites(text)


# ---------------------------------------------------------------------------
# Каждый pattern (Bug 9)
# ---------------------------------------------------------------------------


def test_strip_if_you_want_can_phrase() -> None:
    text = "Готово. Если хочешь, могу добавить таблицу."
    cleaned = strip(text)
    assert "Если хочешь, могу" not in cleaned
    assert "Готово." in cleaned


def test_strip_if_need_can_phrase() -> None:
    text = "Версия 1 — короткая. Если нужно, могу сделать ещё короче."
    cleaned = strip(text)
    assert "Если нужно, могу" not in cleaned
    assert "Версия 1 — короткая." in cleaned


def test_strip_readiness_block() -> None:
    text = "Вот ответ. Готовность блока: проверена в стейджинге."
    cleaned = strip(text)
    assert "Готовность блока" not in cleaned
    assert "Вот ответ." in cleaned


def test_strip_more_versions_phrase() -> None:
    text = "Это вариант. Могу дать ещё 3 версии: A, B, C."
    cleaned = strip(text)
    assert "Могу дать ещё" not in cleaned
    assert "Это вариант." in cleaned


def test_strip_want_can_short_form() -> None:
    text = "Краткий ответ — 42. Хочешь, могу расписать."
    cleaned = strip(text)
    assert "Хочешь, могу" not in cleaned
    assert "Краткий ответ — 42." in cleaned


# ---------------------------------------------------------------------------
# Idempotence + защита кавычек / code block
# ---------------------------------------------------------------------------


def test_strip_is_idempotent() -> None:
    text = "Готово. Если хочешь, могу добавить ещё."
    once = strip(text)
    twice = strip(once)
    assert once == twice


def test_strip_does_not_touch_quoted_phrase() -> None:
    """Внутри «…» паразитная фраза остаётся — это цитата чужого текста."""
    text = 'Юзер написал: «Если хочешь, могу проверить» — это пример паразита.'
    cleaned = strip(text)
    assert "«Если хочешь, могу проверить»" in cleaned


def test_strip_does_not_touch_code_block() -> None:
    text = "Пример:\n```\nif user_wants():\n    return 'если хочешь, могу X'\n```\nКонец."
    cleaned = strip(text)
    assert "если хочешь, могу X" in cleaned
    assert "```" in cleaned


def test_strip_handles_multi_parasite() -> None:
    text = (
        "Ответ готов. Если хочешь, могу добавить таблицу. "
        "Готовность блока: ок. Могу дать ещё 2 версии."
    )
    cleaned = strip(text)
    assert "Если хочешь, могу" not in cleaned
    assert "Готовность блока" not in cleaned
    assert "Могу дать ещё" not in cleaned
    assert "Ответ готов." in cleaned


def test_strip_keeps_normal_text_intact() -> None:
    text = "Тут просто обычный ответ без паразитов. Конец."
    cleaned = strip(text)
    assert cleaned == text


def test_strip_empty_or_blank() -> None:
    assert strip("") == ""
    assert strip("   \n  ").strip() == ""
